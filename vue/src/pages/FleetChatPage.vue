<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { Sidebar } from '@/components/dashboard/sidebar';
import { apiRequest, ApiRequestError, formatApiError } from '@/lib/api';
import { validateExecution, type BrowseExecution } from '@/lib/task-request.mjs';
import { canAutoArrange } from '@/lib/fleet-auto.mjs';

type Model = { id: number; name: string; provider: string };
type Schedule = { kind: 'now' | 'once' | 'daily'; at?: string; timezone?: string };
type Plan = { goal: string; target: {kind: string; name: string}; devices: string[]; schedule: Schedule; membership: string; missed: string; execution?: BrowseExecution };
type Device = {domain: string | null; authorized: boolean; state: string; success: number; failed: number;
  last_task?: {task_id: string; state: string; reason: string | null; answer?: string; scheduled_at: number} | null};
type Run = {task_id: string; device: string; state: string; reason: string | null; answer?: string; scheduled_at: number};
type Job = {id: number; goal: string; state: string; next_at: number | null; schedule: Schedule; schedule_error?: string; devices: Device[]; runs?: Run[]};
type Pending = {request_id: string; plan: Plan};
type Draft = {id: string; message: string; model_id: number; timezone: string; execution?: BrowseExecution;
  question?: string; plan?: Plan; state: string; job_id?: number};

const models = ref<Model[]>([]);
const modelId = ref<number | ''>('');
const message = ref('');
const browse = ref(false);
const duration = ref<number | ''>(7200);
const maxTokens = ref<number | ''>('');
const minObservations = ref<number | ''>('');
const minActive = ref<number | ''>('');
const maxStall = ref<number | ''>('');
const contract = computed(() => ({version:1, kind:'browse', clock:'elapsed', duration_s:duration.value,
  finish_timeout_s:60, stop_timeout_s:30, progress_interval_s:600, event_latency_s:30,
  budget:{max_tokens:maxTokens.value}, progress_requirements:{min_observations:minObservations.value,
    min_active_s:minActive.value, max_stall_s:maxStall.value}}));
const contractError = computed(() => {
  if (!browse.value) return '';
  try { validateExecution(contract.value); return ''; }
  catch (e) { return e instanceof Error ? e.message : '请填写完整的浏览约定。'; }
});
const timezone = ref(Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai');
const plan = ref<Plan | null>(null);
const jobs = ref<Job[]>([]);
const error = ref('');
const note = ref('');
const busy = ref(false);
const pendingKey = ref('');
const draftKey = ref('');
const draftId = ref('');
const pending = ref<Pending | null>(null);
const expanded = ref<number | null>(null);
const runs = ref<Run[]>([]);
let refreshTimer: ReturnType<typeof setInterval> | undefined;
let mounted = true;
const supportedModels = computed(() => models.value.filter(model => ['openai', 'openai_compatible', 'anthropic', 'ollama'].includes(model.provider)));
watch([message, modelId, timezone, browse, duration, maxTokens, minObservations, minActive, maxStall],
  () => { if (!pending.value) plan.value = null; }, {flush:'sync'});
const postHeaders = {'X-Requested-With': 'BK-Task'};
const stateText: Record<string, string> = {bk_running:'等待执行', bk_finished:'本次执行中', bk_done:'本次已结束', bk_stopped:'已停止后续', bk_paused:'需要处理',
  pending:'待执行', running:'执行或核对中', bk_pending:'待执行', bk_active:'执行或核对中', success:'已完成', failed:'未完成', revoked:'已停止', idle:'暂无执行'};
const reasonText: Record<string, string> = {device_offline:'手机离线，本次未执行', missed_schedule:'错过时间，本次未执行',
  device_busy:'手机忙碌，本次未执行', access_revoked:'无设备权限', access_revoked_before_dispatch:'派发前设备权限已撤销',
  device_offline_before_dispatch:'派发前手机离线', schedule_ambiguous_time:'当地时间发生夏令时变化，请重新安排',
  target_ambiguous:'有多个同名目标，请在原任务内容中写明具体设备编号或分组。',
  target_not_found:'没有找到该设备或分组，请在原任务内容中补充准确名称。', group_empty:'这个分组没有可用的授权设备。',
  schedule_invalid:'时间或时区不明确，请在原任务内容中补充。', execution_invalid:'浏览时长或预算不符合要求，请检查填写的执行约定。',
  device_binding_missing:'原计划中的设备绑定已变化，请重新安排。'};
const showTime = (epoch: number | null | undefined) => epoch ? new Date(epoch * 1000).toLocaleString() : '—';
const scheduleText = (s: Schedule) => s.kind === 'now' ? '立即执行' : `${s.kind === 'daily' ? '每天' : '一次'} ${s.at}（${s.timezone}）`;
const hasActive = (job: Job) => job.devices.some(device => ['bk_pending', 'bk_active'].includes(device.last_task?.state || ''));

async function toggleRuns(job: Job) {
  if (expanded.value === job.id) { expanded.value = null; return; }
  try {
    const result = await apiRequest<Job>(`/api/v1/ai/jobs/${job.id}`, {cache:'no-store'});
    runs.value = result.data?.runs || []; expanded.value = job.id;
  } catch (e) { error.value = formatApiError(e, '无法读取执行记录'); }
}

async function refresh() {
  try {
    const result = await apiRequest<{jobs: Job[]}>('/api/v1/ai/jobs', {cache:'no-store'});
    jobs.value = result.data?.jobs || [];
  } catch (e) { error.value = formatApiError(e, '无法读取任务'); }
}

async function prepare() {
  if (busy.value || pending.value || !message.value.trim() || !modelId.value || contractError.value) return;
  busy.value = true; error.value = ''; note.value = ''; plan.value = null;
  try {
    if (!draftId.value) draftId.value = crypto.randomUUID();
    localStorage.setItem(draftKey.value, draftId.value);
    const result = await apiRequest<{plan?: Plan; clarification?: string}>('/api/v1/ai/chat/plan', {
      method:'POST', headers:postHeaders, body:JSON.stringify({message:message.value, model_id:modelId.value,
        timezone:timezone.value, draft_id:draftId.value, ...(browse.value ? {execution:validateExecution(contract.value)} : {})})
    });
    if (result.data?.clarification) note.value = reasonText[result.data.clarification] || result.data.clarification;
    else if (result.data?.plan) {
      plan.value = result.data.plan;
      if (canAutoArrange(message.value, result.data.plan)) {
        busy.value = false;
        await submit();
        return;
      }
    }
    else error.value = '没有取得完整计划，请补充手机和时间。';
  } catch (e) { error.value = formatApiError(e, '无法理解任务，请检查服务器模型'); }
  finally { busy.value = false; }
}

async function submit() {
  if (busy.value || (!plan.value && !pending.value)) return;
  const current = pending.value || {request_id:draftId.value, plan:plan.value!};
  pending.value = current;
  try { localStorage.setItem(pendingKey.value, JSON.stringify(current)); }
  catch { pending.value = null; error.value = '浏览器无法保存本次提交编号，任务尚未安排。请允许本网站保存数据后重试。'; return; }
  busy.value = true; error.value = ''; note.value = '';
  try {
    const response = await apiRequest<Job>('/api/v1/ai/jobs', {method:'POST', headers:postHeaders, body:JSON.stringify(current)});
    pending.value = null; localStorage.removeItem(pendingKey.value); plan.value = null;
    draftId.value = ''; localStorage.removeItem(draftKey.value);
    note.value = `任务 ${response.data?.id ?? ''} 已安排，可在右侧查看状态。`;
    await refresh();
  } catch (e) {
    error.value = formatApiError(e, '任务是否已创建尚待核对');
    if (e instanceof ApiRequestError && e.status === 400 && e.code === 'BK_PLAN' &&
        ['preview_changed', 'group_empty', 'target_not_found', 'device_access_changed', 'group_access_changed', 'schedule_in_past', 'schedule_ambiguous_time'].includes(e.message)) {
      pending.value = null; localStorage.removeItem(pendingKey.value); plan.value = null;
      note.value = '任务尚未创建，请重新理解并安排。';
    } else note.value = '请点“继续核对”查询同一次提交，避免重复创建。';
  } finally { busy.value = false; }
}

async function cancel(job: Job, stopCurrent: boolean) {
  if (busy.value) return;
  busy.value = true; error.value = '';
  try {
    await apiRequest(`/api/v1/ai/jobs/${job.id}/cancel`, {method:'POST', headers:postHeaders,
      body:JSON.stringify({stop_current:stopCurrent})});
    note.value = stopCurrent ? '已请求停止当前任务，手机确认前仍显示执行或核对中。' : '已停止后续安排，当前任务继续。';
    await refresh();
  } catch (e) { error.value = formatApiError(e, '无法停止计划'); }
  finally { busy.value = false; }
}

onMounted(async () => {
  try {
    const result = await apiRequest<{user_id: number; models: Model[]}>('/api/v1/ai/models', {cache:'no-store'});
    if (!result.data?.user_id) throw new Error('无法确认当前用户');
    pendingKey.value = `bk-fleet-pending-v1:${result.data.user_id}`;
    draftKey.value = `bk-fleet-draft-v1:${result.data.user_id}`;
    const saved = localStorage.getItem(pendingKey.value);
    if (saved) pending.value = JSON.parse(saved) as Pending;
    models.value = result.data?.models || [];
    if (supportedModels.value.length === 1) modelId.value = supportedModels.value[0].id;
    draftId.value = localStorage.getItem(draftKey.value) || '';
    if (draftId.value) {
      try {
        const draft = (await apiRequest<Draft>(`/api/v1/ai/chat/drafts/${draftId.value}`, {cache:'no-store'})).data;
        if (draft && draft.state !== 'submitted') {
          message.value = draft.message; modelId.value = draft.model_id; timezone.value = draft.timezone;
          note.value = draft.question ? (reasonText[draft.question] || draft.question) : '已恢复上次尚未安排的任务。';
          if (draft.execution) {
            browse.value = true; duration.value = draft.execution.duration_s; maxTokens.value = draft.execution.budget.max_tokens;
            minObservations.value = draft.execution.progress_requirements.min_observations;
            minActive.value = draft.execution.progress_requirements.min_active_s;
            maxStall.value = draft.execution.progress_requirements.max_stall_s;
          }
          plan.value = draft.plan || null;
        } else if (!pending.value) { draftId.value = ''; localStorage.removeItem(draftKey.value); }
      } catch { draftId.value = ''; localStorage.removeItem(draftKey.value); }
    }
  } catch (e) { error.value = formatApiError(e, '无法读取模型'); }
  await refresh();
  if (mounted) refreshTimer = setInterval(refresh, 10000);
});
onBeforeUnmount(() => { mounted = false; if (refreshTimer) clearInterval(refreshTimer); });
</script>

<template>
  <div class="flex min-h-screen bg-[#f5f5f7] text-gray-900">
    <Sidebar />
    <main class="min-w-0 flex-1 lg:ml-[220px]">
      <header class="border-b border-gray-100 bg-white px-5 py-4">
        <h1 class="text-base font-semibold">智能任务</h1>
        <p class="mt-1 text-sm text-gray-500">用一句话安排手机任务，查看每台手机的执行情况。</p>
      </header>
      <div class="mx-auto grid max-w-7xl gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.9fr)]">
        <section class="rounded-xl border border-gray-200 bg-white p-5" aria-label="安排任务">
          <h2 class="text-base font-semibold">告诉手机做什么</h2>
          <p class="mt-2 text-sm text-gray-600">例如：让一组手机每天晚上八点检查设备状态。明确的立即任务会直接安排，其他情况会先显示识别结果。</p>
          <label class="mt-5 block text-sm font-medium" for="model">服务器理解任务所用模型</label>
          <select id="model" v-model="modelId" :disabled="!!pending || busy" class="mt-2 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm">
            <option value="" disabled>选择服务器模型</option>
            <option v-for="model in supportedModels" :key="model.id" :value="model.id">{{ model.name }}</option>
          </select>
          <p v-if="!supportedModels.length" class="mt-2 text-sm text-amber-700">请先在模型管理中配置兼容的服务器模型。</p>
          <label class="mt-4 block text-sm font-medium" for="timezone">任务时间使用的时区</label>
          <input id="timezone" v-model="timezone" :disabled="!!pending || busy" class="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" />
          <label class="mt-4 block text-sm font-medium" for="message">任务内容</label>
          <textarea id="message" v-model="message" :disabled="!!pending || busy" rows="5" maxlength="4000" class="mt-2 w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm" placeholder="写明手机或分组、要做的事和时间" />
          <label class="mt-3 flex items-center gap-2 text-sm"><input v-model="browse" type="checkbox" :disabled="!!pending || busy" />按约定时长持续浏览</label>
          <div v-if="browse" class="mt-3 grid gap-3 rounded-lg border border-gray-200 p-3 sm:grid-cols-2">
            <p class="text-sm text-gray-600 sm:col-span-2">时长和预算在此填写；任务内容中请描述浏览目标与限制。</p>
            <label class="text-sm">时长（秒）<input v-model.number="duration" type="number" min="1" max="10000" class="mt-1 w-full rounded border border-gray-300 px-2 py-1" /></label>
            <label class="text-sm">模型词元上限<input v-model.number="maxTokens" type="number" min="1" max="10000000" class="mt-1 w-full rounded border border-gray-300 px-2 py-1" /></label>
            <label class="text-sm">最低有效观察次数<input v-model.number="minObservations" type="number" min="2" class="mt-1 w-full rounded border border-gray-300 px-2 py-1" /></label>
            <label class="text-sm">最低有效时间（秒）<input v-model.number="minActive" type="number" min="1" class="mt-1 w-full rounded border border-gray-300 px-2 py-1" /></label>
            <label class="text-sm">允许停滞上限（秒）<input v-model.number="maxStall" type="number" min="1" max="10800" class="mt-1 w-full rounded border border-gray-300 px-2 py-1" /></label>
            <p v-if="contractError" class="text-sm text-amber-700 sm:col-span-2">{{ contractError }}</p>
          </div>
          <button class="mt-3 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50" :disabled="busy || !!pending || !modelId || !message.trim() || !!contractError" @click="prepare">发送任务</button>
          <div v-if="plan || pending" class="mt-5 border-t border-gray-200 pt-4">
            <h3 class="font-semibold">识别出的安排</h3>
            <p class="mt-2 text-sm">{{ (pending?.plan || plan)?.goal }}</p>
            <p class="mt-2 text-sm text-gray-600">手机：{{ (pending?.plan || plan)?.devices.join('、') }}</p>
            <p class="mt-1 text-sm text-gray-600">时间：{{ scheduleText((pending?.plan || plan)!.schedule) }}</p>
            <p v-if="(pending?.plan || plan)?.execution" class="mt-1 text-sm text-gray-600">每台浏览时长：{{ (pending?.plan || plan)?.execution?.duration_s }} 秒；预算上限：{{ (pending?.plan || plan)?.execution?.budget.max_tokens }} 词元。</p>
            <p class="mt-1 text-sm text-gray-500">固定使用以上手机；到点离线或错过时不自动补跑。</p>
            <button class="mt-4 rounded-lg bg-blue-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50" :disabled="busy" @click="submit">{{ pending ? '继续核对' : '安排任务' }}</button>
          </div>
          <p v-if="note" role="status" class="mt-4 text-sm text-blue-800">{{ note }}</p>
          <p v-if="error" role="alert" class="mt-4 text-sm text-red-700">{{ error }}</p>
        </section>
        <section class="rounded-xl border border-gray-200 bg-white p-5" aria-label="任务记录">
          <div class="flex items-center justify-between"><h2 class="text-base font-semibold">任务记录</h2><button class="text-sm text-blue-700" @click="refresh">刷新</button></div>
          <p v-if="!jobs.length" class="mt-5 text-sm text-gray-500">安排任务后，这里会显示各台手机的结果。</p>
          <article v-for="job in jobs" :key="job.id" class="mt-4 border-t border-gray-200 pt-4">
            <div class="flex flex-wrap items-start justify-between gap-2"><p class="font-medium">{{ job.goal }}</p><span class="text-sm text-gray-600">{{ stateText[job.state] || job.state }}</span></div>
            <p class="mt-1 text-sm text-gray-500">{{ scheduleText(job.schedule) }}；下次：{{ showTime(job.next_at) }}</p>
            <p v-if="job.schedule_error" class="mt-1 text-sm text-amber-700">{{ reasonText[job.schedule_error] || job.schedule_error }}</p>
            <ul class="mt-3 space-y-1 text-sm"><li v-for="(device, index) in job.devices" :key="index"><span class="font-medium">{{ device.domain || '已撤销访问' }}</span>：{{ device.last_task ? (reasonText[device.last_task.reason || ''] || stateText[device.last_task.state] || device.last_task.state) : (stateText[device.state] || device.state) }}<span v-if="device.last_task?.answer">；结果：{{ device.last_task.answer }}</span></li></ul>
            <div class="mt-3 flex flex-wrap gap-3 text-sm"><button class="text-blue-700" @click="toggleRuns(job)">{{ expanded === job.id ? '收起执行记录' : '查看执行记录' }}</button><button v-if="['bk_running', 'bk_paused'].includes(job.state)" class="text-blue-700" :disabled="busy" @click="cancel(job, false)">停止后续安排</button><button v-if="hasActive(job)" class="text-red-700" :disabled="busy" @click="cancel(job, true)">同时停止当前任务</button></div>
            <div v-if="expanded === job.id" class="mt-3 border-t border-gray-100 pt-3 text-sm"><p v-if="!runs.length" class="text-gray-500">暂无执行记录。</p><ul class="space-y-2"><li v-for="run in runs" :key="run.task_id">{{ showTime(run.scheduled_at) }} · {{ run.device }} · {{ reasonText[run.reason || ''] || stateText[run.state] || run.state }}<span v-if="run.answer">：{{ run.answer }}</span></li></ul><p v-if="runs.length === 100" class="mt-2 text-gray-500">显示最近 100 次。</p></div>
          </article>
        </section>
      </div>
    </main>
  </div>
</template>
