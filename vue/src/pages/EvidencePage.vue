<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import TaskProcess from '@/components/TaskProcess.vue';
import { apiRequest } from '@/lib/api';
import { useHashPathname, useHashLocation, toHashHref } from '@/lib/hash-router';
const props = defineProps<{ deviceId?: string; taskId?: string }>();
const emit = defineEmits<{ showAll: []; backToTasks: [] }>();
const pathname = useHashPathname();
const location = useHashLocation();
const picked = ref('');
const taskFilter = computed(() => picked.value || (props.deviceId ? props.taskId : new URLSearchParams(location.value.search).get('task')));
watch(() => [props.deviceId, props.taskId, location.value.search], () => { picked.value = ''; });
const domain = ref(props.deviceId || pathname.value.split('/')[2] || '');
type Evidence = { evidence_id: string; task_id: string; phase: string; captured_at_ms: number; size: number; capture_status: string; capture_reason: string; note: string; display_id: number; sha256: string; quiet_ms?: number; quiet_method?: string; observation_scope?: string; run_id?: string; source?: string; capture_timing?: string; late_capture?: boolean; };
type Process = NonNullable<InstanceType<typeof TaskProcess>['$props']['process']>;
type Receipt = InstanceType<typeof TaskProcess>['$props']['receipt'];
type EvidenceReply = { evidence:Evidence[]; sync:{state:string}; process?:Process|null; receipt?:Receipt; runs?:{id:string;goal:string;status:string}[] };
const process = ref<Process|null>(null);
const receipt = ref<Receipt>(null);
const runs = ref<EvidenceReply['runs']>([]);
const items = ref<Evidence[]>([]);
const shown = computed(() => items.value.filter(item => !taskFilter.value || (item.task_id === taskFilter.value && item.source !== 'bk_run_record')));
const error = ref('');
const sync = ref('waiting');
const selected = ref<Evidence | null>(null);
const busy = ref(false);
const phases: Record<string, string> = { step:'步骤截图', before_commit:'提交前', result:'操作结果', terminal:'任务结束', failure:'异常现场', handoff:'人工接管', chat_key:'聊天关键信息', chat_quiet:'静默复查' };
let timer: ReturnType<typeof setInterval>;
let mounted = true;
let serial = 0;
watch(taskFilter, () => { selected.value = null; process.value = null; receipt.value = null; busy.value = false; serial++; void refresh(); });
const isEndCapture = (item: Evidence) => item.source === 'bk_task_display' && item.capture_timing === 'terminal_before_release' && item.late_capture === false;
async function refresh() {
  if (!mounted || busy.value) return;
  busy.value = true;
  const current = ++serial;
  const query = taskFilter.value ? '?task=' + encodeURIComponent(taskFilter.value) : '';
  const url = '/api/v1/device/' + domain.value + '/ai/evidence' + query;
  try {
    const options = () => ({ cache:'no-store' as const, signal:AbortSignal.timeout(15000) });
    const reply = await apiRequest<EvidenceReply>(url, options());
    if (!mounted || current !== serial) return;
    if (!reply.data) throw new Error('未取得证据记录');
    const p = reply.data.process;
    if (p) {
      const total = p.total;
      while (p.events.length < total) {
        const more = await apiRequest<EvidenceReply>(url + '&offset=' + p.events.length, options());
        if (!mounted || current !== serial) return;
        const batch = more.data?.process;
        if (!batch || batch.run_id !== p.run_id || !batch.events.length) throw new Error('过程记录不完整');
        p.events.push(...batch.events);
      }
    }
    process.value = p || null; receipt.value = reply.data.receipt || null; runs.value = reply.data.runs || [];
    items.value = reply.data.evidence; sync.value = reply.data.sync.state; error.value = '';
  } catch { if (mounted && current === serial) error.value = '暂时无法读取证据，请检查登录或连接状态。'; }
  finally { if (current === serial) busy.value = false; }
}
function image(item: Evidence) { return `/api/v1/device/${domain.value}/ai/evidence/${item.evidence_id}/image`; }
onMounted(() => { void refresh(); timer = setInterval(refresh, 15000); });
onBeforeUnmount(() => { mounted = false; clearInterval(timer); });
</script>
<template>
  <main class="p-4 space-y-4" :class="{ 'mx-auto max-w-6xl': !deviceId }">
    <header class="flex flex-wrap items-center justify-between gap-4">
      <div><a v-if="!deviceId" :href="toHashHref('/devices/' + domain)" class="text-sm underline">返回设备详情</a><h1 class="text-lg font-semibold">{{ taskFilter ? '本次任务证据' : '任务证据' }}</h1><p class="mt-1 break-all text-xs text-muted-foreground">{{ domain }}<span v-if="taskFilter"> · {{ taskFilter }}</span></p></div>
      <button class="rounded border px-4 py-2" :disabled="busy" @click="refresh">刷新证据</button>
    </header>
    <div v-if="!taskFilter && runs?.length" class="space-y-2"><h2 class="font-semibold">按任务查看过程</h2>
      <button v-for="run in runs" :key="run.id" class="block text-left underline" @click="picked = run.id">{{ run.goal }}</button></div>
    <button v-if="picked" class="text-sm underline" @click="picked = ''">返回全部证据</button>
    <TaskProcess v-if="taskFilter" :domain="domain" :process="process" :receipt="receipt" />
    <p role="status">同步：{{ sync === 'connected' ? '连接正常' : sync === 'retry_pending' ? '等待重连补传' : '等待同步' }} · {{ shown.length }} 条记录</p>
    <p v-if="deviceId" class="flex gap-4 text-sm"><button class="underline" @click="emit('backToTasks')">返回手机任务</button><button v-if="taskFilter" class="underline" @click="emit('showAll')">查看全部证据</button></p>
    <p v-else class="text-sm"><a :href="toHashHref('/ai/' + domain)" class="underline">返回手机任务</a><a v-if="taskFilter" :href="toHashHref('/evidence/' + domain)" class="ml-4 underline">查看全部证据</a></p>
    <p v-if="error" role="alert" class="text-red-600">{{ error }}</p>
    <p v-if="!shown.length && !error && !process && !receipt" class="rounded border p-8 text-muted-foreground">还没有归档证据。未归档、采集失败和连接中断均不能当作已取得结束截图；请查看原任务的证据状态。</p>
    <section v-if="deviceId && selected" class="rounded border bg-muted p-3" aria-label="证据大图">
      <button class="mb-2 rounded border bg-background px-3 py-1.5 text-sm" @click="selected = null">关闭大图</button>
      <img :src="image(selected)" class="mx-auto max-h-[65dvh] max-w-full object-contain" alt="关键截图原图" />
    </section>
    <section v-else class="grid gap-4" style="grid-template-columns: repeat(auto-fill, minmax(min(220px, 100%), 1fr))">
      <article v-for="item in shown" :key="item.evidence_id" class="overflow-hidden rounded-lg border bg-card">
        <button v-if="item.size > 0" class="block w-full bg-muted p-2" @click="selected = item" :aria-label="'查看' + (phases[item.phase] || item.phase) + '截图'">
          <img :src="image(item)" loading="lazy" class="mx-auto h-64 object-contain" alt="任务关键截图" />
        </button>
        <div v-else class="p-6 bg-muted text-sm">截图未取得：{{ item.capture_reason }}。此记录不会触发业务重做。</div>
        <div class="space-y-2 p-4 text-sm"><strong>{{ isEndCapture(item) ? '任务结束截图' : phases[item.phase] || item.phase }}</strong> · 已归档 / 待人工核验
          <p>{{ new Date(item.captured_at_ms).toLocaleString() }}</p><p class="break-words">{{ item.note }}</p>
          <p v-if="item.late_capture === true" class="text-amber-700">此图为结束后补采，不能作为结束瞬间证据。</p>
          <p v-else-if="isEndCapture(item)" class="text-muted-foreground">手机在任务结束、释放执行显示前采集。</p>
          <p v-if="item.phase === 'chat_quiet' && item.quiet_ms === 300000 && item.quiet_method === 'verified_profile_ui_and_pixels_v1' && item.size > 0" class="text-emerald-700">手机已记录连续5分钟复查，并核对会话身份、可见内容与画面。{{ item.observation_scope === 'empty_visible_conversation' ? '可见会话列表为空，期间未观察到新增内容；不代表完整历史已加载。' : '范围：当时完整可见的会话内容。' }}截图仍可人工复核。</p>
          <details><summary class="cursor-pointer">追溯信息</summary><p class="break-all">任务：{{ item.task_id }}</p><p>设备：{{ domain }}</p><p v-if="item.run_id" class="break-all">执行编号：{{ item.run_id }}</p><p>执行屏：{{ item.display_id }}</p><p>来源：{{ item.source === 'bk_task_display' ? '手机任务执行显示' : item.source || '历史记录未提供来源' }}</p><p class="break-all">文件校验值：{{ item.sha256 || '无图' }}</p></details>
        </div>
      </article>
    </section>
    <div v-if="selected && !deviceId" role="dialog" aria-modal="true" aria-label="证据大图" class="fixed inset-0 z-50 flex flex-col items-center bg-black/90 p-4" @click.self="selected = null" @keydown.esc="selected = null">
      <button autofocus class="mb-3 rounded bg-white px-6 py-2 text-black" @click="selected = null">关闭大图</button>
      <img :src="image(selected)" class="min-h-0 max-h-full max-w-full object-contain" alt="关键截图原图" />
    </div>
  </main>
</template>
