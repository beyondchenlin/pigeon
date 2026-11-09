<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { ArrowLeft, ExternalLink, RefreshCw, Smartphone, Battery, Monitor, Cpu, Wifi, AppWindow } from 'lucide-vue-next';
import { Sidebar } from '@/components/dashboard/sidebar';
import { Button } from '@/components/ui/button';
import PhoneControl from '@/components/devices/PhoneControl.vue';
import { apiRequest } from '@/lib/api';
import { useHashPathname, useHashRouter } from '@/lib/hash-router';
import { useDeviceTelemetry, bytes } from '@/lib/device-telemetry';
import { useDeviceChannel } from '@/lib/device-channel';

const pathname = useHashPathname();
const id = computed(() => decodeURIComponent(pathname.value.split('/').pop() || ''));
const router = useHashRouter();
const record = ref<Record<string, any>>({});
const recordError = ref('');
const online = computed(() => record.value.state === 'online');
const { data, stale, sampledTime, loading, refresh } = useDeviceTelemetry(id, online);
const { status, info, displays, connected, stale: statusStale, sampled, logs, command, log } = useDeviceChannel(id, online);
const tab = ref('properties');
const tabs = [{ id: 'properties', label: '设备属性' }, { id: 'performance', label: '性能' }, { id: 'files', label: '文件' }, { id: 'logs', label: '连接日志' }];
const title = computed(() => record.value.comment || data.value.system?.model || info.value.model || record.value.model || id.value);
const currentDisplay = computed(() => displays.value.find(d => d.id === 0 || d.displayId === 0) || displays.value[0] || {});
const rotation = computed(() => Number(info.value.displayRotation ?? currentDisplay.value.rotation ?? 0));
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
function yes(v: unknown, a: string, b: string) { return typeof v === 'boolean' ? v ? a : b : '—'; }
function time(v: unknown) { return typeof v === 'number' && v > 0 ? new Date(v * 1000).toLocaleString() : '—'; }
const sections = computed(() => [
  { title: '基本信息', icon: Smartphone, rows: [
    ['设备 ID', id.value], ['品牌 / 型号', [data.value.system?.manufacturer || record.value.brand, data.value.system?.model || info.value.model || record.value.model].filter(Boolean).join(' / ') || '—'],
    ['Android', text(data.value.system?.android || info.value.version)], ['SDK', text(data.value.system?.sdk || record.value.sdk)],
    ['处理器架构', text(data.value.system?.abi || info.value.arch || record.value.abi)], ['服务版本', text(record.value.version)],
    ['本次开机', text(info.value.boot)], ['注册时间', time(record.value.register_time)]
  ] },
  { title: '电池与状态', icon: Battery, rows: [
    ['电量', data.value.battery?.percent == null ? '—' : `${data.value.battery.percent}%${stale.value ? '（过期）' : ''}`],
    ['充电状态', ({ 2: '充电中', 3: '放电中', 4: '未充电', 5: '已充满' } as Record<number,string>)[data.value.battery?.status ?? 0] || '—'],
    ['供电方式', ({ battery: '电池', AC: '电源', USB: 'USB', Wireless: '无线充电', Dock: '底座' } as Record<string,string>)[data.value.battery?.source || ''] || '—'],
    ['电池温度', fixed(data.value.battery?.temperature_c, ' °C')], ['电压', fixed(data.value.battery?.voltage_v, ' V', 3)],
    ['健康状态', ({ 2: '良好', 3: '过热', 4: '失效', 5: '电压过高', 6: '故障', 7: '过冷' } as Record<number,string>)[data.value.battery?.health ?? 0] || '—'],
    ['屏幕状态', yes(info.value.screenOn, '已亮屏', '已熄屏')], ['锁定状态', yes(status.value.locked, '已锁定', '已解锁')]
  ] },
  { title: '屏幕', icon: Monitor, rows: [
    ['系统分辨率', data.value.display?.width && data.value.display?.height ? `${data.value.display.width} × ${data.value.display.height}` : '—'],
    ['屏幕密度', data.value.display?.density ? `${data.value.display.density} dpi` : '—'],
    ['旋转方向', info.value.displayRotation != null || currentDisplay.value.rotation != null ? `${rotation.value * 90}°` : '—'],
    ['显示屏数量', displays.value.length ? String(displays.value.length) : '—']
  ] },
  { title: '性能与存储', icon: Cpu, rows: [
    ['CPU 使用率', fixed(status.value.cpu_percent, '%')], ['CPU 核心', text(status.value.cpu_count)],
    ['内存使用', status.value.mem_used != null && status.value.mem_total != null ? `${bytes(status.value.mem_used)} / ${bytes(status.value.mem_total)}` : '—'],
    ['可用内存', bytes(status.value.mem_available)],
    ['存储使用', data.value.storage?.used != null ? `${bytes(data.value.storage.used)} / ${bytes(data.value.storage.total)}` : '—'],
    ['可用存储', bytes(data.value.storage?.available)]
  ] },
  { title: '连接信息', icon: Wifi, rows: [
    ['设备状态', recordError.value ? '读取失败' : online.value ? '在线' : record.value.state === 'pending' ? '未接入' : '离线'],
    ['控制通道', connected.value ? '已连接' : '未连接'], ['网络接口', text(status.value.net_io_iface)],
    ['局域网 IP', text(status.value.default_ip)], ['公网 IP', text(record.value.public_ip)],
    ['最近心跳', time(record.value.last_heartbeat_time)]
  ] },
  { title: '当前应用', icon: AppWindow, rows: [
    ['应用名称', text(info.value.curApplication?.label)], ['应用包名', text(info.value.curApplication?.pkg)],
    ['当前界面', text(info.value.curApplication?.activity)]
  ] }
]);
const performance = computed(() => [
  { name: 'CPU 使用率', value: status.value.cpu_percent, detail: `${text(status.value.cpu_count)} 核 · ${fixed(status.value.cpu_freq_current, ' MHz', 0)}` },
  { name: '内存使用率', value: status.value.mem_percent, detail: `${bytes(status.value.mem_used)} / ${bytes(status.value.mem_total)}` },
  { name: '存储使用率', value: data.value.storage?.total && data.value.storage?.used != null ? data.value.storage.used / data.value.storage.total * 100 : null, detail: `${bytes(data.value.storage?.used)} / ${bytes(data.value.storage?.total)}` }
]);
function send(cmd: string, payload: unknown) { if (command(cmd, payload)) log(cmd === 'device/wakeup' ? '已发送唤醒指令' : '已发送手机导航指令'); }
onMounted(() => { void load(); timer = setInterval(() => void load(), 15000); });
onBeforeUnmount(() => { disposed = true; controller?.abort(); clearInterval(timer); });
</script>

<template>
  <div class="min-h-screen bg-[#f5f5f7] text-foreground dark:bg-background">
    <Sidebar />
    <main class="min-w-0 lg:ml-[220px]">
      <header class="flex min-h-14 flex-wrap items-center justify-between gap-2 border-b border-border bg-background px-5 py-2">
        <div class="flex min-w-0 items-center gap-3"><Button variant="ghost" size="icon" aria-label="返回设备列表" @click="router.push('/devices')"><ArrowLeft class="h-4 w-4" /></Button><h1 class="truncate text-base font-semibold">{{ title }}</h1><span class="text-xs" :class="online ? 'text-emerald-600' : 'text-muted-foreground'">● {{ online ? '在线' : '离线' }}</span></div>
        <div class="flex items-center gap-2"><a :href="`/d/${id}/`" target="_blank" rel="noopener" class="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-orange-600">原版远控<ExternalLink class="h-3 w-3" /></a><Button size="sm" variant="outline" class="text-xs" @click="router.push(`/devices/${id}`)">设备详情与权限</Button></div>
      </header>
      <p v-if="recordError" role="alert" class="m-4 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">{{ recordError }} <button class="underline" @click="load">重试</button></p>
      <div class="grid items-start gap-4 p-4 xl:grid-cols-[minmax(260px,340px)_minmax(0,1fr)]">
        <div class="mx-auto w-full max-w-[390px] xl:sticky xl:top-4 xl:max-w-none">
          <PhoneControl :device-id="id" :online="online" :command-ready="connected" :rotation="rotation" @command="send" @log="log" />
          <p class="mt-2 truncate px-1 text-xs text-muted-foreground">{{ id }}</p>
        </div>
        <section class="min-w-0 overflow-hidden rounded-lg border border-border bg-background">
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4">
            <div class="flex gap-5" role="tablist" aria-label="设备信息"><button v-for="item in tabs" :id="`tab-${item.id}`" :key="item.id" role="tab" :aria-selected="tab === item.id" :aria-controls="`panel-${item.id}`" class="border-b-2 py-3.5 text-sm" :class="tab === item.id ? 'border-orange-500 font-medium text-orange-600' : 'border-transparent text-muted-foreground hover:text-foreground'" @click="tab = item.id">{{ item.label }}</button></div>
            <Button variant="ghost" size="sm" class="gap-1 text-xs" :disabled="loading" @click="refresh"><RefreshCw class="h-3 w-3" :class="{ 'animate-spin': loading }" />刷新数据</Button>
          </div>
          <div class="flex flex-wrap gap-x-4 gap-y-1 border-b bg-muted/20 px-4 py-2 text-[11px] text-muted-foreground">
            <span :class="{ 'text-amber-600': stale }">电池 / 存储：{{ sampledTime }}{{ stale ? ' · 未取得最新数据' : ' · 手机系统实测' }}</span>
            <span :class="{ 'text-amber-600': statusStale }">运行状态：{{ sampled ? new Date(sampled).toLocaleTimeString() : '—' }}{{ statusStale ? ' · 未取得最新数据' : ' · 实时' }}</span>
          </div>
          <div :id="`panel-${tab}`" role="tabpanel" :aria-labelledby="`tab-${tab}`">
            <div v-if="tab === 'properties'" class="grid gap-x-6 px-5 py-1 2xl:grid-cols-2">
              <section v-for="section in sections" :key="section.title" class="min-w-0 border-b border-border py-4 last:border-0">
                <h2 class="mb-3 flex items-center gap-2 text-sm font-semibold"><component :is="section.icon" class="h-4 w-4 text-orange-500" />{{ section.title }}</h2>
                <dl class="grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs leading-5"><template v-for="[label, value] in section.rows" :key="label"><dt class="text-muted-foreground">{{ label }}</dt><dd class="min-w-0 break-all" :data-property="label">{{ value }}</dd></template></dl>
              </section>
              <p class="py-3 text-[11px] text-muted-foreground 2xl:col-span-2">“—” 表示设备尚未提供该数据；断连时保留上次读数并标记过期。</p>
            </div>
            <div v-else-if="tab === 'performance'" class="space-y-6 p-5">
              <section v-for="metric in performance" :key="metric.name"><div class="mb-2 flex justify-between text-sm"><span>{{ metric.name }}</span><span class="tabular-nums">{{ fixed(metric.value, '%') }}</span></div><div class="h-2 overflow-hidden rounded bg-muted"><div class="h-full rounded bg-orange-400" :style="{ width: `${Math.max(0, Math.min(100, Number(metric.value) || 0))}%` }" /></div><p class="mt-2 text-xs text-muted-foreground">{{ metric.detail }}</p></section>
              <dl class="grid grid-cols-2 gap-3 border-t pt-4 text-xs"><dt class="text-muted-foreground">核心温度</dt><dd>{{ fixed(status.core_temperature, ' °C') }}</dd><dt class="text-muted-foreground">本次开机累计发送</dt><dd>{{ bytes(status.net_io_bytes_sent) }}</dd><dt class="text-muted-foreground">本次开机累计接收</dt><dd>{{ bytes(status.net_io_bytes_recv) }}</dd></dl>
              <Button variant="outline" size="sm" @click="router.push(`/devices/${id}`)">查看历史性能与应用统计</Button>
            </div>
            <div v-else-if="tab === 'files'" class="p-3"><p class="mb-3 text-xs text-muted-foreground">手机文件管理 · 应用与更多工具可从右上角“原版远控”进入。</p><iframe v-if="online" :src="`/d/${id}/fs/?mode=embed`" title="手机文件管理" class="h-[650px] w-full rounded border" /><p v-else class="p-8 text-center text-sm text-muted-foreground">设备离线，暂时无法浏览文件。</p></div>
            <div v-else class="min-h-[300px] p-5"><p class="mb-4 text-xs text-muted-foreground">本页连接与操作记录，最多保留最近 80 条。</p><ol class="space-y-3 text-xs"><li v-for="(entry, index) in logs" :key="index" class="flex gap-4"><time class="shrink-0 tabular-nums text-muted-foreground">{{ entry.time }}</time><span>{{ entry.message }}</span></li></ol></div>
          </div>
        </section>
      </div>
    </main>
  </div>
</template>
