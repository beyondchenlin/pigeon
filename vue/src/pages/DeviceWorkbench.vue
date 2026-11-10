<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, type Component } from 'vue';
import { ArrowLeft, ExternalLink, RefreshCw, MoreHorizontal, Maximize, Minimize, Minimize2, X, Smartphone, Battery, Monitor, Cpu, Wifi, AppWindow, LockKeyhole, LockKeyholeOpen, Sun, Moon, Copy, Check, ChevronRight } from 'lucide-vue-next';
import { Sidebar } from '@/components/dashboard/sidebar';
import { Button } from '@/components/ui/button';
import PhoneControl from '@/components/devices/PhoneControl.vue';
import PhoneComposer from '@/components/devices/PhoneComposer.vue';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useMobileViewport } from '@/lib/mobile-layout';
import { apiRequest } from '@/lib/api';
import { useHashPathname, useHashRouter } from '@/lib/hash-router';
import { useDeviceTelemetry, bytes } from '@/lib/device-telemetry';
import { useDeviceChannel } from '@/lib/device-channel';

const pathname = useHashPathname();
const id = computed(() => pathname.value.startsWith('/control/') ? decodeURIComponent(pathname.value.split('/').pop() || '') : window.location.pathname.match(/^\/d\/([a-z0-9]+)\/$/)?.[1] || decodeURIComponent(pathname.value.split('/').pop() || ''));
const router = useHashRouter();
const record = ref<Record<string, any>>({});
const recordError = ref('');
const mobile = useMobileViewport();
const controlsVisible = ref(true);
const phone = ref<InstanceType<typeof PhoneControl>>();
const detailsElement = ref<HTMLElement>();
const toolsOpen = ref(false), composerOpen = ref(false), switchOpen = ref(false), textBusy = ref(false);
const actionNotice = ref('');
const browserFullscreen = ref(false);
const fullscreenBusy = ref(false);
let ownsFullscreen = false;
function fullscreenChanged() {
  browserFullscreen.value = document.fullscreenElement === document.documentElement;
  if (!browserFullscreen.value) { ownsFullscreen = false; controlsVisible.value = true; }
}
async function toggleBrowserFullscreen() {
  if (fullscreenBusy.value) return;
  actionNotice.value = '';
  if (!document.fullscreenEnabled || !document.documentElement.requestFullscreen) {
    actionNotice.value = '此浏览器暂不支持网页全屏，已按可见区域适配画面。'; return;
  }
  fullscreenBusy.value = true;
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else {
      // Fullscreen the document, not just the canvas: portalled input/tool
      // dialogs must remain usable. Must run directly from a user's click.
      await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
      ownsFullscreen = true; controlsVisible.value = false;
    }
    toolsOpen.value = false;
  } catch { actionNotice.value = '浏览器未允许全屏，请再点一次“全屏”；当前画面仍会自动适配。'; }
  finally { fullscreenBusy.value = false; }
}
const availableDevices = ref<{ domain: string; comment?: string; model?: string; state: string }[]>([]);
const switchingLoading = ref(false), switchError = ref(''), switchQuery = ref('');
function openComposer() {
  if (phone.value?.isTyping()) { actionNotice.value = '文字正在写入手机，请稍后再打开输入面板。'; return; }
  phone.value?.releaseKeyboard(); toolsOpen.value = false; composerOpen.value = true;
}
function showDetails() {
  toolsOpen.value = false;
  if (detailsElement.value instanceof HTMLDialogElement) detailsElement.value.showModal();
  else { controlsVisible.value = true; detailsElement.value?.scrollIntoView({ block: 'nearest' }); }
}
async function showSwitch() {
  switchOpen.value = true; toolsOpen.value = false; switchingLoading.value = true; switchError.value = '';
  try { const response = await apiRequest<{ data: typeof availableDevices.value }>('/api/v1/device?size=100&page=1', { signal: AbortSignal.timeout(10000) }); availableDevices.value = response.data?.data || []; }
  catch { switchError.value = '读取设备列表失败，请重试或返回设备管理。'; }
  finally { switchingLoading.value = false; }
}
function chooseDevice(domain: string) { if (!textBusy.value) { switchOpen.value = false; router.push(`/control/${encodeURIComponent(domain)}`); } }
function escapeTools(e: KeyboardEvent) { if (e.key === 'Escape') controlsVisible.value = true; }

const online = computed(() => record.value.state === 'online');
const { data, stale, sampledTime, loading, refresh } = useDeviceTelemetry(id, online);
const { status, info, displays, connected, stale: statusStale, sampled, logs, command, requestCommand, log } = useDeviceChannel(id, online);
const tab = ref('properties');
const tabs = [{ id: 'properties', label: '设备属性' }, { id: 'performance', label: '性能' }, { id: 'files', label: '文件' }, { id: 'logs', label: '连接日志' }];
const manufacturer = computed(() => data.value.system?.manufacturer || record.value.brand || '');
const model = computed(() => data.value.system?.model || info.value.model || record.value.model || '');
const title = computed(() => [manufacturer.value, model.value].filter(Boolean).join(' ') || record.value.comment || id.value);
const subtitle = computed(() => [record.value.comment, id.value].filter(Boolean).join(' · '));
const currentDisplay = computed(() => displays.value.find(d => d.id === 0 || d.displayId === 0) || displays.value[0] || {});
const rotation = computed(() => Number(info.value.displayRotation ?? currentDisplay.value.rotation ?? 0));
const batteryStatus = computed(() => ({ 2: '充电中', 3: '放电中', 4: '未充电', 5: '已充满' } as Record<number, string>)[data.value.battery?.status ?? 0] || '状态未知');
const batteryPercent = computed(() => percent(data.value.battery?.percent));
const copied = ref(false);
const copyMessage = ref('');
let copyTimer: ReturnType<typeof setTimeout>;
let timer: ReturnType<typeof setInterval>;
let disposed = false;
let busy = false;
let controller: AbortController | undefined;
async function load() {
  if (busy || document.hidden) return;
  busy = true; controller = new AbortController();
  const timeout = setTimeout(() => controller?.abort(), 12000);
  try {
    const result = await apiRequest<Record<string, any>>(`/api/v1/device/${encodeURIComponent(id.value)}`, { signal: controller.signal, cache: 'no-store' });
    if (!disposed) { record.value = result.data || {}; recordError.value = ''; }
  } catch { if (!disposed) recordError.value = '设备状态读取失败，请检查连接后重试。'; }
  finally { clearTimeout(timeout); busy = false; }
}
function text(v: unknown) { return v === null || v === undefined || v === '' ? '—' : String(v); }
function fixed(v: unknown, suffix = '', digits = 1) { return typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(digits)}${suffix}` : '—'; }
function yes(v: unknown, a: string, b: string) { return typeof v === 'boolean' ? v ? a : b : '状态未知'; }
function time(v: unknown) { return typeof v === 'number' && v > 0 ? new Date(v * 1000).toLocaleString() : '—'; }
function percent(v: unknown) { return typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100 ? v : null; }
function ratio(used: unknown, total: unknown) { return typeof used === 'number' && typeof total === 'number' && total > 0 ? percent(used / total * 100) : null; }
type PropertyRow = { label: string; value: string; meter?: number | null; copy?: boolean; stale?: boolean };
type PropertySection = { title: string; icon: Component; rows: PropertyRow[] };
const row = (label: string, value: unknown, extra: Omit<PropertyRow, 'label' | 'value'> = {}): PropertyRow => ({ label, value: text(value), ...extra });
const sections = computed<PropertySection[]>(() => [
  { title: '基本信息', icon: Smartphone, rows: [
    row('品牌 / 型号', [manufacturer.value, model.value].filter(Boolean).join(' / ')), row('设备 ID', id.value, { copy: true }),
    row('Android', data.value.system?.android || info.value.version), row('SDK', data.value.system?.sdk || record.value.sdk),
    row('处理器架构', data.value.system?.abi || info.value.arch || record.value.abi), row('服务版本', record.value.version)
  ] },
  { title: '电池与状态', icon: Battery, rows: [
    row('电量', batteryPercent.value == null ? '—' : `${batteryPercent.value}%`, { meter: batteryPercent.value, stale: stale.value }),
    row('充电状态', batteryStatus.value),
    row('供电方式', ({ battery: '电池', AC: '电源', USB: 'USB', Wireless: '无线充电', Dock: '底座' } as Record<string, string>)[data.value.battery?.source || '']),
    row('电池温度', fixed(data.value.battery?.temperature_c, ' °C')), row('电压', fixed(data.value.battery?.voltage_v, ' V', 3)),
    row('健康状态', ({ 2: '良好', 3: '过热', 4: '失效', 5: '电压过高', 6: '故障', 7: '过冷' } as Record<number, string>)[data.value.battery?.health ?? 0])
  ] },
  { title: '屏幕', icon: Monitor, rows: [
    row('系统分辨率', data.value.display?.width && data.value.display?.height ? `${data.value.display.width} × ${data.value.display.height}` : '—'),
    row('屏幕密度', data.value.display?.density ? `${data.value.display.density} dpi` : '—'),
    row('旋转方向', info.value.displayRotation != null || currentDisplay.value.rotation != null ? `${rotation.value * 90}°` : '—'),
    row('显示设备', displays.value.length ? `${displays.value.length}（含虚拟显示）` : '—')
  ] },
  { title: '性能与存储', icon: Cpu, rows: [
    row('CPU 使用率', fixed(status.value.cpu_percent, '%')), row('CPU 核心', status.value.cpu_count),
    row('内存使用', status.value.mem_used != null && status.value.mem_total != null ? `${bytes(status.value.mem_used)} / ${bytes(status.value.mem_total)}` : '—', { meter: ratio(status.value.mem_used, status.value.mem_total), stale: statusStale.value }),
    row('可用内存', bytes(status.value.mem_available)),
    row('存储使用', data.value.storage?.used != null ? `${bytes(data.value.storage.used)} / ${bytes(data.value.storage.total)}` : '—', { meter: ratio(data.value.storage?.used, data.value.storage?.total), stale: stale.value }),
    row('可用存储', bytes(data.value.storage?.available))
  ] },
  { title: '连接信息', icon: Wifi, rows: [
    row('设备状态', recordError.value ? '读取失败' : online.value ? '在线' : record.value.state === 'pending' ? '未接入' : '离线'),
    row('控制通道', connected.value ? '已连接' : '未连接'), row('网络接口', status.value.net_io_iface)
  ] },
  { title: '当前应用', icon: AppWindow, rows: [
    row('应用名称', info.value.curApplication?.label), row('应用包名', info.value.curApplication?.pkg)
  ] }
]);
const technical = computed(() => [
  row('本次开机', info.value.boot), row('注册时间', time(record.value.register_time)),
  row('局域网 IP', status.value.default_ip), row('公网 IP', record.value.public_ip),
  row('最近心跳', time(record.value.last_heartbeat_time)), row('当前界面', info.value.curApplication?.activity)
]);
const performance = computed(() => [
  { name: 'CPU 使用率', value: percent(status.value.cpu_percent), detail: `${text(status.value.cpu_count)} 核 · ${fixed(status.value.cpu_freq_current, ' MHz', 0)}` },
  { name: '内存使用率', value: ratio(status.value.mem_used, status.value.mem_total), detail: `${bytes(status.value.mem_used)} / ${bytes(status.value.mem_total)}` },
  { name: '存储使用率', value: ratio(data.value.storage?.used, data.value.storage?.total), detail: `${bytes(data.value.storage?.used)} / ${bytes(data.value.storage?.total)}` }
]);
async function copyId() {
  clearTimeout(copyTimer);
  try { await navigator.clipboard.writeText(id.value); copied.value = true; copyMessage.value = '设备 ID 已复制'; }
  catch { copyMessage.value = '复制失败，请选择设备 ID 后手动复制'; }
  copyTimer = setTimeout(() => { copied.value = false; copyMessage.value = ''; }, 3000);
}
function selectTab(index: number) { const item = tabs[(index + tabs.length) % tabs.length]; if (item) { tab.value = item.id; document.getElementById(`tab-${item.id}`)?.focus(); } }
function send(cmd: string, payload: unknown) { if (command(cmd, payload)) log(cmd === 'device/wakeup' ? '已发送唤醒指令' : '已发送手机导航指令'); }
onMounted(() => { document.addEventListener('keydown', escapeTools); document.addEventListener('fullscreenchange', fullscreenChanged); fullscreenChanged(); void load(); timer = setInterval(() => void load(), 15000); });
onBeforeUnmount(() => { document.removeEventListener('keydown', escapeTools); document.removeEventListener('fullscreenchange', fullscreenChanged); if (ownsFullscreen && document.fullscreenElement === document.documentElement) void document.exitFullscreen().catch(() => {}); disposed = true; controller?.abort(); clearInterval(timer); clearTimeout(copyTimer); });
</script>

<template>
  <div class="device-workbench bg-[#f5f5f7] text-foreground dark:bg-background" :class="{ 'workbench-clean': !controlsVisible }">
    <Sidebar />
    <main class="workbench-main">
      <header v-show="controlsVisible" class="workbench-header border-b border-border bg-background">
        <div class="device-heading">
          <Button variant="ghost" size="icon" aria-label="返回设备列表" class="shrink-0" @click="router.push('/devices')"><ArrowLeft class="h-4 w-4" /></Button>
          <div class="min-w-0">
            <div class="flex min-w-0 items-center gap-3"><h1 class="truncate text-lg font-semibold" :title="title">{{ title }}</h1><span class="device-state" :class="online && !recordError ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950' : 'bg-muted text-muted-foreground'">● {{ recordError ? '状态待确认' : online ? '在线' : '离线' }}</span></div>
            <p class="mt-0.5 truncate text-xs text-muted-foreground" :title="subtitle">{{ subtitle }}</p>
          </div>
        </div>
        <div class="header-actions"><Button size="sm" variant="ghost" :disabled="fullscreenBusy" :aria-label="browserFullscreen ? '退出浏览器全屏' : '进入浏览器全屏'" @click="toggleBrowserFullscreen"><component :is="browserFullscreen ? Minimize : Maximize" class="h-3.5 w-3.5" /><span>{{ browserFullscreen ? '退出全屏' : '全屏' }}</span></Button><Button size="sm" variant="ghost" :disabled="textBusy" @click="showSwitch">切换 ▾</Button><button class="hide-tools" aria-label="隐藏远控工具" @click="controlsVisible = false"><Minimize2 class="h-4 w-4" /></button></div>
      </header>
      <p v-if="recordError" role="alert" class="mx-4 mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">{{ recordError }} <button class="underline" @click="load">重试</button></p>
      <div class="workbench-layout">
        <PhoneControl ref="phone" class="workbench-phone" immersive :controls-visible="controlsVisible" :text-busy="textBusy" @hide-tools="controlsVisible = false" @compose="openComposer" @tools="toolsOpen = true" :device-id="id" :online="online" :command-ready="connected" :request-command="requestCommand" :rotation="rotation" @command="send" @log="log" />
        <component :is="mobile ? 'dialog' : 'section'" ref="detailsElement" class="workbench-details rounded-lg border border-border bg-background" aria-label="设备信息与工具">
          <div v-if="mobile" class="flex items-center justify-between border-b p-3"><strong class="text-sm">设备信息与工具</strong><button aria-label="关闭设备信息" class="p-2" @click="(detailsElement as HTMLDialogElement)?.close()"><X class="h-4 w-4" /></button></div>
          <div v-if="!mobile" class="flex flex-wrap gap-2 border-b p-3"><Button size="sm" @click="openComposer">文字输入</Button><Button size="sm" variant="outline" @click="toolsOpen = true">应用与更多工具</Button></div>
          <div class="details-toolbar border-b border-border">
            <div class="details-tabs" role="tablist" aria-label="设备信息">
              <button v-for="(item, index) in tabs" :id="`tab-${item.id}`" :key="item.id" role="tab" :aria-selected="tab === item.id" :aria-controls="`panel-${item.id}`" :tabindex="tab === item.id ? 0 : -1" class="border-b-2 py-3.5 text-sm" :class="tab === item.id ? 'border-orange-500 font-semibold text-orange-600' : 'border-transparent text-muted-foreground hover:text-foreground'" @click="tab = item.id" @keydown.right.prevent="selectTab(index + 1)" @keydown.left.prevent="selectTab(index - 1)" @keydown.home.prevent="selectTab(0)" @keydown.end.prevent="selectTab(tabs.length - 1)">{{ item.label }}</button>
            </div>
            <Button variant="ghost" size="sm" class="gap-1 text-xs" :disabled="loading || !online" @click="refresh"><RefreshCw class="h-3.5 w-3.5" :class="{ 'animate-spin': loading }" />刷新数据</Button>
          </div>
          <div class="status-strip border-b border-border bg-muted/10" aria-label="手机当前状态">
            <div class="status-badges">
              <span :class="{ 'text-amber-700 dark:text-amber-400': stale }" data-battery-summary><Battery class="h-4 w-4" :class="{ 'text-emerald-600': !stale }" />{{ batteryPercent == null ? '电量未知' : `${batteryPercent}%` }} · {{ batteryStatus }}<small v-if="stale">{{ data.sampled_at ? '（上次读数）' : '（待采集）' }}</small></span>
              <span :class="{ 'text-muted-foreground': statusStale }"><component :is="status.locked === false ? LockKeyholeOpen : LockKeyhole" class="h-4 w-4" />{{ yes(status.locked, '已锁定', '已解锁') }}<small v-if="statusStale && sampled">（上次）</small></span>
              <span :class="{ 'text-muted-foreground': statusStale }"><component :is="info.screenOn === false ? Moon : Sun" class="h-4 w-4 text-orange-500" />{{ yes(info.screenOn, '已亮屏', '已熄屏') }}<small v-if="statusStale && sampled">（上次）</small></span>
            </div>
            <div class="sample-times text-muted-foreground">
              <span :class="{ 'text-amber-700 dark:text-amber-400': stale }">电池 / 存储 {{ sampledTime }} · {{ stale ? '未取得最新数据' : '手机系统采集' }}</span>
              <span :class="{ 'text-amber-700 dark:text-amber-400': statusStale }">运行状态 {{ sampled ? new Date(sampled).toLocaleTimeString() : '—' }} · {{ statusStale ? '未取得最新数据' : '实时' }}</span>
            </div>
          </div>
          <div :id="`panel-${tab}`" :key="tab" class="details-scroll" role="tabpanel" :aria-labelledby="`tab-${tab}`" tabindex="0">
            <div v-if="tab === 'properties'" class="properties-content">
              <div class="property-grid">
                <section v-for="section in sections" :key="section.title" class="property-section">
                  <h2 class="flex items-center gap-2 text-sm font-semibold"><component :is="section.icon" class="h-4 w-4 text-orange-500" />{{ section.title }}</h2>
                  <dl class="property-rows">
                    <div v-for="property in section.rows" :key="property.label" class="property-row">
                      <dt class="text-muted-foreground">{{ property.label }}</dt>
                      <dd :data-property="property.label">
                        <div class="property-value"><span>{{ property.value }}</span><button v-if="property.copy" type="button" :aria-label="copied ? '设备 ID 已复制' : '复制设备 ID'" :title="copied ? '已复制' : '复制设备 ID'" class="copy-button text-muted-foreground hover:text-orange-600" @click="copyId"><component :is="copied ? Check : Copy" class="h-3.5 w-3.5" /></button></div>
                        <div v-if="property.meter != null" class="property-meter bg-muted" role="meter" :aria-label="property.label" :aria-valuenow="property.meter" :aria-valuemin="0" :aria-valuemax="100" :aria-valuetext="property.value + (property.stale ? '（上次读数）' : '')"><div :class="property.stale ? 'bg-amber-400' : 'bg-emerald-500'" :style="{ width: `${property.meter}%` }" /></div>
                      </dd>
                    </div>
                  </dl>
                </section>
              </div>
              <p v-if="copyMessage" role="status" class="py-2 text-xs text-muted-foreground">{{ copyMessage }}</p>
              <details class="technical-details border-b border-border">
                <summary class="flex cursor-pointer items-center gap-2 py-3 text-sm font-medium"><ChevronRight class="technical-chevron h-4 w-4 text-muted-foreground" />更多技术信息</summary>
                <dl class="technical-grid pb-3"><div v-for="property in technical" :key="property.label" class="property-row"><dt class="text-muted-foreground">{{ property.label }}</dt><dd :data-property="property.label">{{ property.value }}</dd></div></dl>
              </details>
              <p class="py-3 text-[11px] text-muted-foreground">“—” 表示尚未取得数据；断连时保留上次读数，以上方采集时间为准。</p>
            </div>
            <div v-else-if="tab === 'performance'" class="space-y-6 p-5">
              <section v-for="metric in performance" :key="metric.name"><div class="mb-2 flex justify-between text-sm"><span>{{ metric.name }}</span><span class="tabular-nums">{{ fixed(metric.value, '%') }}</span></div><div v-if="metric.value != null" class="h-2 overflow-hidden rounded bg-muted"><div class="h-full rounded bg-emerald-500" :style="{ width: `${metric.value}%` }" /></div><p class="mt-2 text-xs text-muted-foreground">{{ metric.detail }}</p></section>
              <dl class="grid grid-cols-2 gap-3 border-t pt-4 text-xs"><dt class="text-muted-foreground">核心温度</dt><dd>{{ fixed(status.core_temperature, ' °C') }}</dd><dt class="text-muted-foreground">本次开机累计发送</dt><dd>{{ bytes(status.net_io_bytes_sent) }}</dd><dt class="text-muted-foreground">本次开机累计接收</dt><dd>{{ bytes(status.net_io_bytes_recv) }}</dd></dl>
              <Button variant="outline" size="sm" @click="router.push(`/devices/${id}`)">查看历史性能与应用统计</Button>
            </div>
            <div v-else-if="tab === 'files'" class="file-panel"><p class="text-xs text-muted-foreground">手机文件管理 · 应用与更多工具可从右上角“更多工具”进入。</p><iframe v-if="online" :src="`/d/${id}/fs/?mode=embed`" title="手机文件管理" class="file-frame rounded border" /><p v-else class="p-8 text-center text-sm text-muted-foreground">设备离线，暂时无法浏览文件。</p></div>
            <div v-else class="p-5"><p class="mb-4 text-xs text-muted-foreground">本页连接与操作记录，最多保留最近 80 条。</p><ol class="space-y-3 text-xs"><li v-for="(entry, index) in logs" :key="index" class="flex gap-4"><time class="shrink-0 tabular-nums text-muted-foreground">{{ entry.time }}</time><span>{{ entry.message }}</span></li></ol></div>
          </div>
        </component>
      </div>
    </main>
    <button v-if="!controlsVisible" class="restore-tools" aria-label="展开远控工具" @click="controlsVisible = true"><MoreHorizontal class="h-4 w-4" /><span>工具</span></button>
    <p v-if="actionNotice" class="workbench-notice" role="status" @click="actionNotice = ''">{{ actionNotice }}</p>
    <PhoneComposer v-model:open="composerOpen" :device-id="id" :title="title" :connected="connected" :request="requestCommand" @busy="textBusy = $event" />
    <Dialog v-model:open="toolsOpen"><DialogContent><DialogHeader><DialogTitle>手机工具 · {{ title }}</DialogTitle></DialogHeader><div class="remote-tool-grid">
      <Button variant="outline" :disabled="fullscreenBusy" @click="toggleBrowserFullscreen">{{ browserFullscreen ? '退出浏览器全屏' : '全屏隐藏浏览器栏' }}</Button>
      <Button variant="outline" :disabled="!connected || textBusy" @click="send('device/wakeup', []); toolsOpen = false">唤醒手机</Button>
      <Button variant="outline" @click="phone?.reconnect(); toolsOpen = false">重连画面</Button>
      <Button variant="outline" @click="showDetails">设备属性与文件</Button><Button variant="outline" @click="showSwitch">切换手机</Button>
      <Button variant="outline" @click="openComposer">文字输入</Button><Button variant="outline" @click="toolsOpen = false; controlsVisible = false">纯画面查看</Button>
      <a :href="`/d/${id}/?native=1`" target="_blank" rel="noopener" class="rounded border p-3 text-center text-sm">原生应用与更多工具 ↗</a><Button variant="outline" @click="router.push(`/devices/${id}`)">设备详情与权限</Button>
    </div><p class="text-xs text-muted-foreground">点击画面一次只隐藏工具；收起后正常操作手机。右侧“工具”可随时恢复。</p><p class="text-xs text-muted-foreground">{{ phone?.mode === 'webrtc' ? 'WebRTC' : '兼容传屏' }} · {{ phone?.fps ?? 0 }} FPS · 媒体 RTT {{ phone?.mediaRtt ?? '—' }} ms</p></DialogContent></Dialog>
    <Dialog v-model:open="switchOpen"><DialogContent><DialogHeader><DialogTitle>切换手机</DialogTitle></DialogHeader><input v-model="switchQuery" class="rounded border p-2 text-base" placeholder="搜索设备名称或 ID" aria-label="搜索要切换的设备" /><p v-if="switchError" role="alert">{{ switchError }} <button @click="showSwitch">重试</button></p><p v-if="switchingLoading">正在读取设备…</p><div class="max-h-[50dvh] space-y-1 overflow-auto"><button v-for="item in availableDevices.filter(d => `${d.domain} ${d.comment} ${d.model}`.toLowerCase().includes(switchQuery.toLowerCase()))" :key="item.domain" class="flex min-h-12 w-full items-center justify-between gap-3 rounded border px-3 py-2 text-left text-sm" :disabled="textBusy" @click="chooseDevice(item.domain)"><span class="truncate">{{ item.comment || item.model || item.domain }}</span><span class="shrink-0 text-xs text-muted-foreground">{{ item.state === 'online' ? '在线' : '离线' }}{{ item.domain === id ? ' · 当前' : '' }}</span></button></div></DialogContent></Dialog>
  </div>
</template>

<style scoped>
.device-workbench { min-height: 100dvh; }
.workbench-main { position: relative; min-width: 0; }
.workbench-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 12px 20px; flex-shrink: 0; }
.device-heading { display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1 1 280px; }
.device-state { flex-shrink: 0; border-radius: 5px; padding: 2px 7px; font-size: 12px; line-height: 18px; }
.header-actions { display: flex; align-items: center; gap: 16px; }
.workbench-layout { display: grid; gap: 16px; padding: 16px; min-height: 0; min-width: 0; }
.workbench-phone { width: 100%; max-width: 440px; justify-self: center; height: min(860px, 88dvh); min-height: 440px; }
.workbench-details { min-width: 0; min-height: 0; overflow: hidden; display: flex; flex-direction: column; container-type: inline-size; }
.details-toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0 12px; padding: 0 16px; flex-shrink: 0; }
.details-tabs { display: flex; gap: 22px; }
.details-tabs button { white-space: nowrap; }
.status-strip { padding: 12px 18px 9px; display: flex; flex-wrap: wrap; align-items: center; gap: 9px 20px; flex-shrink: 0; }
.status-badges { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 20px; font-size: 12px; }
.status-badges > span { display: inline-flex; align-items: center; gap: 6px; }
.status-badges small { font-size: 10px; }
.sample-times { display: flex; flex-wrap: wrap; gap: 3px 16px; font-size: 10px; line-height: 16px; }
.details-scroll { min-height: 0; min-width: 0; }
.properties-content { padding: 0 18px; }
.property-grid { display: grid; grid-template-columns: minmax(0, 1fr); column-gap: 26px; }
.property-section { min-width: 0; padding: 16px 0; border-bottom: 1px solid var(--border); }
.property-section h2 { margin-bottom: 10px; }
.property-rows { display: grid; gap: 3px; }
.property-row { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 10px; align-items: start; min-width: 0; font-size: 12px; line-height: 22px; padding: 2px 0; }
.property-row dd { min-width: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.property-value { display: flex; align-items: center; gap: 6px; }
.copy-button { display: inline-flex; flex-shrink: 0; padding: 4px; border-radius: 3px; }
.property-meter { height: 4px; max-width: 180px; border-radius: 4px; overflow: hidden; margin: 4px 0; }
.property-meter > div { height: 100%; border-radius: inherit; }
.technical-details summary { list-style: none; }
.technical-details summary::-webkit-details-marker { display: none; }
.technical-details[open] .technical-chevron { transform: rotate(90deg); }
.technical-grid { display: grid; gap: 4px 26px; }
.file-panel { display: flex; flex-direction: column; gap: 12px; padding: 16px; height: 100%; min-height: 420px; }
.file-frame { width: 100%; flex: 1; min-height: 360px; }
button:focus-visible, a:focus-visible, summary:focus-visible, .details-scroll:focus-visible { outline: 2px solid #f97316; outline-offset: 3px; }
@container (min-width: 670px) { .property-grid, .technical-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@container (min-width: 960px) { .status-strip { justify-content: space-between; } .sample-times { flex-direction: column; align-items: flex-end; } .property-row { font-size: 13px; grid-template-columns: 104px minmax(0, 1fr); } }
@media (min-width: 1024px) { .workbench-main { margin-left: 220px; } }
@media (min-width: 1200px) {
  .device-workbench { height: 100dvh; overflow: hidden; }
  .workbench-main { display: flex; flex-direction: column; height: 100%; }
  .workbench-layout { flex: 1; grid-template-columns: clamp(320px, 27vw, 440px) minmax(0, 1fr); overflow: hidden; }
  .workbench-phone { height: 100%; min-height: 0; max-width: none; }
  .details-scroll { overflow: auto; overscroll-behavior: contain; scrollbar-gutter: stable; flex: 1; }
}
@media (max-width: 599px) {
  .workbench-header { padding: 12px; }
  .device-heading { gap: 6px; }
  .header-actions { margin-left: 46px; flex-wrap: wrap; gap: 12px; }
  .workbench-layout { padding: 12px; gap: 12px; }
  .workbench-phone { height: min(760px, 85dvh); }
  .details-toolbar { padding: 0 12px; }
  .details-tabs { gap: 18px; }
  .status-strip { padding: 12px; }
  .properties-content { padding: 0 12px; }
}
@media(min-width:768px) and (max-width:1023px){.device-workbench:not(.workbench-clean){padding-top:50px;padding-bottom:58px}}
</style>

<style scoped>
.hide-tools{width:40px;height:40px;display:grid;place-items:center;border-radius:5px}.restore-tools{position:fixed;z-index:30;right:0;top:43%;width:44px;min-height:52px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;background:color-mix(in srgb,var(--background) 95%,transparent);border:1px solid var(--border);border-radius:10px 0 0 10px;font-size:10px;box-shadow:0 2px 10px #00000012}.workbench-notice{position:fixed;z-index:60;bottom:80px;left:50%;transform:translateX(-50%);width:max-content;max-width:90vw;background:var(--background);border:1px solid var(--border);border-radius:7px;padding:10px;font-size:12px}.remote-tool-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.remote-tool-grid>*{min-height:44px;font-size:12px}.workbench-clean .workbench-details{display:none}.workbench-clean :deep(.bk-mobile-header),.workbench-clean :deep(.bk-mobile-nav),.workbench-clean :deep(aside){display:none}.workbench-clean .workbench-main{margin-left:0;height:var(--bk-viewport-height,100dvh)}.workbench-clean .workbench-layout{display:block;height:100%;padding:0}.workbench-clean .workbench-phone{width:100%;max-width:none;height:100%;min-height:0}
@media(max-width:767px){
 .device-workbench{position:fixed;top:var(--bk-viewport-top,0px);left:var(--bk-viewport-left,0px);width:var(--bk-viewport-width,100%);height:var(--bk-viewport-height,100dvh);min-height:0;overflow:hidden}
 .device-workbench :deep(.bk-mobile-header),.device-workbench :deep(.bk-mobile-nav){display:none}
 .workbench-main{height:100%;margin:0}.workbench-layout{height:100%;display:block;padding:0}
 .workbench-phone{position:relative;width:100%;height:100%;max-width:none;min-height:0}
 .workbench-header{position:absolute;top:max(8px,env(safe-area-inset-top));left:8px;right:8px;z-index:20;gap:4px;padding:3px 5px;flex-wrap:nowrap;border:1px solid var(--border);border-radius:10px;background:color-mix(in srgb,var(--background) 95%,transparent);box-shadow:0 2px 12px #00000010}
 .device-heading{flex:1 1 0;gap:3px}.device-heading h1{font-size:12px;line-height:18px}.device-heading>div>div{gap:4px}.device-heading p{font-size:9px;max-width:47vw}.device-heading>button{width:34px;height:44px}.device-state{font-size:9px;padding:1px 3px;line-height:15px}.header-actions{margin:0;gap:2px;flex-wrap:nowrap}.header-actions button{font-size:11px;padding:4px;min-height:40px}.header-actions .hide-tools{width:32px}
 .workbench-details{position:fixed;top:calc(var(--bk-viewport-top,0px) + 20px);left:calc(var(--bk-viewport-left,0px) + 10px);right:auto;bottom:auto;margin:0;width:calc(var(--bk-viewport-width,100vw) - 20px);height:calc(var(--bk-viewport-height,100dvh) - 40px);max-height:calc(var(--bk-viewport-height,100dvh) - 40px);max-width:none;padding:0;z-index:50;overflow:auto}
 .workbench-details:not([open]){display:none}.workbench-details::backdrop{background:#0006}.workbench-details[open]{display:flex}.details-scroll{overflow:auto;flex:1;min-height:0}.details-tabs{gap:14px}.details-tabs button{font-size:12px}.details-toolbar{padding:0 10px}.status-strip{padding:10px;font-size:11px}.property-row{grid-template-columns:88px minmax(0,1fr)}
}
.restore-tools{top:calc(var(--bk-viewport-top,0px) + var(--bk-viewport-height,100dvh)*.43);right:var(--bk-viewport-right,0px)}
.workbench-notice{bottom:calc(var(--bk-viewport-bottom,0px) + 80px)}
@media(min-width:768px) and (max-width:1199px){.device-workbench{height:var(--bk-viewport-height,100dvh)}.workbench-main{height:100%;display:flex;flex-direction:column}.workbench-layout{flex:1;min-height:0;grid-template-columns:minmax(220px,40%) minmax(0,1fr)}.workbench-phone{height:100%;min-height:0}.details-scroll{overflow:auto;flex:1}.workbench-clean{padding:0}}
</style>
