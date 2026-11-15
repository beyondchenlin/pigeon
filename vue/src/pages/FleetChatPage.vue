<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { Send, MessageSquare, Smartphone, Users, Settings2, Bot, ClipboardList, RefreshCw } from 'lucide-vue-next';
import { Sidebar } from '@/components/dashboard/sidebar';
import { apiRequest, ApiRequestError, formatApiError } from '@/lib/api';
import { validateExecution, type BrowseExecution } from '@/lib/task-request.mjs';
import { canAutoArrange } from '@/lib/fleet-auto.mjs';
import { toHashHref, useHashPathname, useHashSearchParams } from '@/lib/hash-router';
import AiTasksPage from '@/pages/AiTasksPage.vue';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

type Model = { id: number; name: string; provider: string };
type Schedule = { kind: 'now' | 'once' | 'daily' | 'weekly' | 'monthly'; at?: string; timezone?: string; weekday?: number; day?: number };
type Plan = { goal: string; target: {kind: string; name: string}; devices: string[]; schedule: Schedule; membership: string; missed: string; execution?: BrowseExecution };
type Device = {domain: string | null; authorized: boolean; state: string; success: number; failed: number;
  active_task?: {task_id: string; status: string; cancel_requested: boolean; phone_received_at?: number | null} | null;
  last_task?: {task_id: string; state: string; reason: string | null; answer?: string; scheduled_at: number} | null};
type Run = {task_id: string; device: string; state: string; reason: string | null; answer?: string; scheduled_at: number};
type Job = {id: number; goal: string; state: string; next_at: number | null; schedule: Schedule; schedule_error?: string; devices: Device[]; runs?: Run[]};
type Pending = {request_id: string; plan: Plan};
type Draft = {id: string; message: string; model_id: number; timezone: string; execution?: BrowseExecution;
  question?: string; plan?: Plan; state: string; job_id?: number};
type Target = {domain:string; name:string; state:string};
type TargetGroup = {id:number; name:string; count:number};
type ChatMessage = {id:string; role:'user'|'assistant'; text:string; jobId?:number; selectedLabel?:string};

const props = withDefaults(defineProps<{ workbenchDevice?: string; manuallyHeld?: boolean; active?: boolean }>(), { active: true });
const emit = defineEmits<{ viewEvidence: [taskId: string] }>();
const routePath = useHashPathname();
const deviceId = ref(props.workbenchDevice || /^\/ai\/([a-z0-9]+)$/.exec(routePath.value)?.[1] || '');
type DeviceReceipt = { task_id:string; goal:string; status:string; cancel_requested?:boolean; created_at:number };
const deviceReceipts = ref<DeviceReceipt[]>([]);
const deviceReceiptError = ref('');
const deviceConnection = ref('正在检查手机任务连接…');
async function refreshDeviceConnection() {
  if (!deviceId.value) return;
  try { const result = await apiRequest<{state:string}>(`/api/v1/device/${encodeURIComponent(deviceId.value)}/ai/native-discovery`, {cache:'no-store', signal:AbortSignal.timeout(15000)});
    const labels:Record<string,string> = {connected:'手机任务连接正常',not_installed:'未安装新版 BK CLAW',not_ready:'手机任务连接未就绪',offline:'手机离线',auth_failed:'手机任务连接认证失败',incompatible:'手机任务接口不兼容',guard_not_ready:'接管保护尚未就绪',install_unconfirmed:'保护安装状态未确认'};
    deviceConnection.value = labels[result.data?.state || ''] || '手机任务连接状态待核对';
  } catch { deviceConnection.value = '手机任务连接状态暂时无法读取'; }
}
const selectedReceipt = ref('');
const receiptOpen = ref(false);
const receiptLabels: Record<string,string> = {submitting:'正在下达',starting:'正在启动',queued:'排队中',running:'执行中',paused:'已暂停',waiting_for_answer:'需要补充信息',waiting_on_phone:'需要手机处理',unknown:'状态待核对',finishing:'手机收尾中',finish_overdue:'收尾超期',done:'执行结束',failed:'未完成',cancelled:'已停止',rejected:'未开始'};
const deviceJobs = computed(() => deviceId.value ? jobs.value.filter(job => job.devices.some(d => d.domain === deviceId.value)) : jobs.value);
const matchesDevice = (p: Plan) => !deviceId.value || (p?.target?.kind === 'device' && p.target.name === deviceId.value && Array.isArray(p.devices) && p.devices.length === 1 && p.devices[0] === deviceId.value);
function showReceipt(id:string) { selectedReceipt.value = id; receiptOpen.value = true; }
async function refreshReceipts() {
  if (!deviceId.value) return;
  try { const result = await apiRequest<{tasks:DeviceReceipt[]}>(`/api/v1/device/${encodeURIComponent(deviceId.value)}/ai/tasks`, {cache:'no-store',signal:AbortSignal.timeout(15000)});
    if (!Array.isArray(result.data?.tasks)) throw new Error('未取得任务记录');
    const rows = [...result.data.tasks];
    // A lost response can leave an original direct task outside the list window.
    // Keep its original ID visible so the existing recovery flow can query it.
    try {
      const saved = JSON.parse(sessionStorage.getItem(`bk-task-pending:${deviceId.value}`) || 'null');
      if (saved && /^[a-f0-9-]{36}$/.test(saved.taskId) && !rows.some(row => row.task_id === saved.taskId)) {
        rows.unshift({task_id:saved.taskId, goal:'待核对的原任务', status:'unknown', created_at:0});
      }
    } catch { /* Invalid local receipts never trigger a new task. */ }
    deviceReceipts.value = rows; deviceReceiptError.value = '';
  } catch { deviceReceiptError.value = '暂时无法读取本机任务记录，请刷新重试。'; }
}
const models = ref<Model[]>([]);
const modelId = ref<number | ''>('');
const message = ref('');
const transcript = ref<ChatMessage[]>([]);
const transcriptKey = ref('');
const conversationText = ref('');
const conversationPane = ref<HTMLElement | null>(null);
const targets = ref<Target[]>([]);
const groups = ref<TargetGroup[]>([]);
const targetTab = ref<'device'|'group'>('device');
const selectedTarget = ref<{kind:'device'|'group'; name:string; label:string} | null>(null);
const awaitingTarget = ref(false);
const showSettings = ref(false);
const showTargetChoices = computed(() => awaitingTarget.value && !pending.value && !plan.value);
function addMessage(role:'user'|'assistant', text:string, jobId?:number) {
  transcript.value.push({id:crypto.randomUUID(), role, text, ...(jobId ? {jobId} : {})});
  transcript.value = transcript.value.slice(-100);
  try { if (transcriptKey.value) localStorage.setItem(transcriptKey.value, JSON.stringify(transcript.value)); } catch { /* Draft identity is managed separately. */ }
  void nextTick(() => { if (conversationPane.value) conversationPane.value.scrollTop = conversationPane.value.scrollHeight; });
}
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
const hashParams = useHashSearchParams();
const targetJob = computed(() => {
  const value = hashParams.value.get('job') || '';
  return /^[1-9]\d{0,9}$/.test(value) ? value : '';
});
const targetJobError = ref('');
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
watch([modelId, timezone, browse, duration, maxTokens, minObservations, minActive, maxStall],
  () => { if (!pending.value) plan.value = null; }, {flush:'sync'});
const postHeaders = {'X-Requested-With': 'BK-Task'};
const stateText: Record<string, string> = {bk_running:'等待执行', bk_finished:'本次执行中', bk_done:'本次已结束', bk_stopped:'已停止后续', bk_paused:'需要处理',
  pending:'待执行', running:'执行或核对中', bk_pending:'待执行', bk_active:'执行或核对中', success:'已完成', failed:'未完成', revoked:'已停止', idle:'暂无执行'};
const activeStateText: Record<string, string> = {starting:'正在启动', submitting:'正在派发', queued:'等待执行', running:'执行中', paused:'已暂停',
  waiting_for_answer:'需要补充信息', waiting_on_phone:'需要在手机处理', unknown:'状态待核对', finishing:'手机收尾中', finish_overdue:'收尾超期，正在核对'};
const reasonText: Record<string, string> = {device_offline:'手机离线，本次未执行', missed_schedule:'错过时间，本次未执行',
  manual_control:'人工接管中，本次任务未下发', device_busy:'手机忙碌，本次未执行', access_revoked:'无设备权限', access_revoked_before_dispatch:'派发前设备权限已撤销',
  device_offline_before_dispatch:'派发前手机离线', schedule_ambiguous_time:'当地时间发生夏令时变化，请重新安排',
  target_ambiguous:'有多个同名目标，请在原任务内容中写明具体设备编号或分组。',
  target_not_found:'没有找到该设备或分组，请在原任务内容中补充准确名称。', group_empty:'这个分组没有可用的授权设备。',
  schedule_invalid:'时间或时区不明确，请在原任务内容中补充。', execution_invalid:'浏览时长或预算不符合要求，请检查填写的执行约定。',
  device_binding_missing:'原计划中的设备绑定已变化，请重新安排。'};
function manualSkip(job: Job) {
  const targets = job.devices.filter(d => !deviceId.value || d.domain === deviceId.value);
  return job.state === 'bk_done' && targets.length > 0 && targets.every(d => d.last_task?.reason === 'manual_control');
}
const showTime = (epoch: number | null | undefined) => epoch ? new Date(epoch * 1000).toLocaleString() : '—';
const scheduleText = (s: Schedule) => {
  if (s.kind === 'now') return '立即执行';
  const label = s.kind === 'daily' ? '每天' : s.kind === 'weekly' ? `每周${['一','二','三','四','五','六','日'][(s.weekday || 1) - 1]}` :
    s.kind === 'monthly' ? `每月 ${s.day} 日（没有该日期的月份跳过）` : '一次';
  return `${label} ${s.at}（${s.timezone}）`;
};
const hasActive = (job: Job) => job.devices.some(device => !!device.active_task || ['bk_pending', 'bk_active'].includes(device.last_task?.state || ''));

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
    const rows = result.data?.jobs || [];
    const requested = targetJob.value;
    if (requested && !rows.some(job => String(job.id) === requested)) {
      try {
        const target = await apiRequest<Job>(`/api/v1/ai/jobs/${requested}`, {cache:'no-store'});
        if (target.data && String(target.data.id) === requested) rows.unshift(target.data);
        targetJobError.value = '';
      } catch { targetJobError.value = '无法读取指定任务，请检查任务是否存在以及当前账号是否有权限。'; }
    } else targetJobError.value = '';
    jobs.value = requested ? [...rows].sort((a, b) => Number(String(b.id) === requested) - Number(String(a.id) === requested)) : rows;
  } catch (e) { error.value = formatApiError(e, '无法读取任务'); }
}

async function prepare(selection?: {kind:'device'|'group'; name:string; label:string}) {
  const input = selection ? selection.label : message.value.trim();
  if (busy.value || pending.value || !input || !modelId.value || contractError.value) return;
  const fullMessage = conversationText.value ? `${conversationText.value}\n补充：${selection ? selection.name : input}` : deviceId.value ? `目标设备：${deviceId.value}。\n${input}` : input;
  if (fullMessage.length > 4000) { error.value = '这段任务描述太长，请开启一项新任务后简短描述。'; return; }
  if (selection && awaitingTarget.value) {
    const question = [...transcript.value].reverse().find(item => item.role === 'assistant' && !item.jobId);
    if (question) question.selectedLabel = selection.label;
  }
  addMessage('user', input);
  message.value = '';
  conversationText.value = fullMessage;
  awaitingTarget.value = false;
  busy.value = true; error.value = ''; note.value = ''; plan.value = null;
  try {
    if (!draftId.value) draftId.value = crypto.randomUUID();
    localStorage.setItem(draftKey.value, draftId.value);
    const result = await apiRequest<{plan?: Plan; clarification?: string}>('/api/v1/ai/chat/plan', {
      method:'POST', headers:postHeaders, body:JSON.stringify({message:fullMessage, model_id:modelId.value,
        timezone:timezone.value, draft_id:draftId.value, ...(deviceId.value ? {selected_target:{kind:'device', name:deviceId.value}} : selection ? {selected_target:{kind:selection.kind, name:selection.name}} : {}), ...(browse.value ? {execution:validateExecution(contract.value)} : {})})
    });
    if (result.data?.clarification) {
      note.value = reasonText[result.data.clarification] || result.data.clarification;
      addMessage('assistant', note.value);
      awaitingTarget.value = /设备|手机|分组|哪台|哪一组|target_|group_empty/i.test(result.data.clarification);
      if (awaitingTarget.value) await loadTargets();
    }
    else if (result.data?.plan) {
      if (!matchesDevice(result.data.plan)) { error.value = '任务目标与当前设备不一致，请重新描述。'; return; }
      plan.value = result.data.plan;
      addMessage('assistant', '我整理好了这次任务，请核对设备和执行时间。');
      if (!deviceId.value && !selection && fullMessage === input && canAutoArrange(fullMessage, result.data.plan)) {
        busy.value = false;
        await submit();
        return;
      }
    }
    else error.value = '没有取得完整计划，请补充手机和时间。';
  } catch (e) { error.value = formatApiError(e, '无法理解任务，请检查服务器模型'); addMessage('assistant', error.value); }
  finally { busy.value = false; }
}

async function submit() {
  if (busy.value || (!plan.value && !pending.value)) return;
  const current = pending.value || {request_id:draftId.value, plan:plan.value!};
  if (props.manuallyHeld && current.plan.schedule.kind === 'now' && !pending.value) {
    error.value = '人工接管中，本次任务未下发。交还手机后可再次确认。';
    addMessage('assistant', error.value);
    return;
  }
  if (!matchesDevice(current.plan)) { error.value = '原任务目标与当前设备不一致，不能在此提交。'; return; }
  pending.value = current;
  try { localStorage.setItem(pendingKey.value, JSON.stringify(current)); }
  catch { pending.value = null; error.value = '浏览器无法保存本次提交编号，任务尚未安排。请允许本网站保存数据后重试。'; return; }
  busy.value = true; error.value = ''; note.value = '';
  try {
    const response = await apiRequest<Job>('/api/v1/ai/jobs', {method:'POST', headers:postHeaders, body:JSON.stringify(current)});
    pending.value = null; localStorage.removeItem(pendingKey.value); plan.value = null;
    draftId.value = ''; localStorage.removeItem(draftKey.value);
    note.value = `任务 ${response.data?.id ?? ''} 已安排，可在右侧查看状态。`;
    if (response.data?.id) addMessage('assistant', '任务已安排。执行状态会在下面的卡片中更新。', response.data.id);
    conversationText.value = ''; selectedTarget.value = null; awaitingTarget.value = false;
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

async function loadTargets() {
  try {
    const result = await apiRequest<{devices:Target[]; groups:TargetGroup[]}>('/api/v1/ai/targets', {cache:'no-store'});
    targets.value = result.data?.devices || []; groups.value = result.data?.groups || [];
  } catch { targets.value = []; groups.value = []; error.value = '暂时无法读取设备列表，请直接在聊天中输入设备名称或编号。'; }
}

function chooseTarget() {
  if (!selectedTarget.value) return;
  const chosen = selectedTarget.value;
  selectedTarget.value = null;
  void prepare(chosen);
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
    const scope = deviceId.value ? `:device:${deviceId.value}` : '';
    pendingKey.value = `bk-fleet-pending-v1:${result.data.user_id}${scope}`;
    draftKey.value = `bk-fleet-draft-v1:${result.data.user_id}${scope}`;
    transcriptKey.value = `bk-fleet-chat-v1:${result.data.user_id}${scope}`;
    try { const savedChat = JSON.parse(localStorage.getItem(transcriptKey.value) || '[]'); if (Array.isArray(savedChat)) transcript.value = savedChat.slice(-100); } catch { /* Ignore invalid local history. */ }
    const saved = localStorage.getItem(pendingKey.value);
    if (saved) pending.value = JSON.parse(saved) as Pending;
    models.value = result.data?.models || [];
    if (supportedModels.value.length === 1) modelId.value = supportedModels.value[0].id;
    draftId.value = localStorage.getItem(draftKey.value) || '';
    if (draftId.value) {
      try {
        const draft = (await apiRequest<Draft>(`/api/v1/ai/chat/drafts/${draftId.value}`, {cache:'no-store'})).data;
        if (draft && draft.state !== 'submitted') {
          conversationText.value = draft.message; modelId.value = draft.model_id; timezone.value = draft.timezone;
          note.value = draft.question ? (reasonText[draft.question] || draft.question) : '已恢复上次尚未安排的任务。';
          if (draft.execution) {
            browse.value = true; duration.value = draft.execution.duration_s; maxTokens.value = draft.execution.budget.max_tokens;
            minObservations.value = draft.execution.progress_requirements.min_observations;
            minActive.value = draft.execution.progress_requirements.min_active_s;
            maxStall.value = draft.execution.progress_requirements.max_stall_s;
          }
          plan.value = draft.plan && matchesDevice(draft.plan) ? draft.plan : null;
          if (draft.plan && !matchesDevice(draft.plan)) error.value = '恢复的草稿目标与当前设备不一致，请重新安排。';
          awaitingTarget.value = !!draft.question && /设备|手机|分组|哪台|哪一组|target_|group_empty/i.test(draft.question);
          if (awaitingTarget.value) await loadTargets();
        } else if (!pending.value) { draftId.value = ''; localStorage.removeItem(draftKey.value); }
      } catch { draftId.value = ''; localStorage.removeItem(draftKey.value); }
    }
  } catch (e) { error.value = formatApiError(e, '无法读取模型'); }
  const receipt = hashParams.value.get('task') || '';
  if (deviceId.value && /^[a-f0-9-]{36}$/.test(receipt)) showReceipt(receipt);
  await Promise.all([refresh(), refreshReceipts(), refreshDeviceConnection()]);
  if (mounted) refreshTimer = setInterval(() => { if (props.active) { void refresh(); void refreshReceipts(); } }, 10000);
});
watch(() => props.active, active => {
  if (active) { void refresh(); void refreshReceipts(); }
});
onBeforeUnmount(() => { mounted = false; if (refreshTimer) clearInterval(refreshTimer); });
</script>

<template>
  <div class="flex bg-[#f7f9f8] text-[#1d3028]" :class="workbenchDevice ? 'embedded-chat' : 'min-h-screen'">
    <Sidebar v-if="!workbenchDevice" />
    <main v-show="!workbenchDevice || !receiptOpen" class="flex min-w-0 flex-1 flex-col" :class="{ 'min-h-screen lg:ml-[220px]': !workbenchDevice }">
      <header class="flex flex-wrap items-center justify-between gap-3 border-b border-[#e4ece7] bg-white px-6 py-4">
        <div><h1 class="text-lg font-semibold">智能任务</h1><p class="mt-1 text-xs text-[#72847a]">{{ deviceId ? `当前设备：${deviceId}` : '像聊天一样安排手机工作' }}</p></div>
        <button class="flex items-center gap-1.5 rounded-lg border border-[#dce8e0] px-3 py-2 text-xs text-[#486255]" @click="showSettings = !showSettings"><Settings2 class="h-4 w-4" />任务设置</button>
      </header>
      <div v-if="showSettings" class="grid gap-3 border-b border-[#e4ece7] bg-white px-6 py-4 text-sm sm:grid-cols-2">
        <label>理解任务的模型<select v-model="modelId" :disabled="!!pending || busy" class="mt-1 w-full rounded-lg border border-[#dbe5de] bg-white p-2"><option value="" disabled>选择服务器模型</option><option v-for="model in supportedModels" :key="model.id" :value="model.id">{{ model.name }}</option></select></label>
        <label>任务时间的时区<input v-model="timezone" :disabled="!!pending || busy" class="mt-1 w-full rounded-lg border border-[#dbe5de] p-2" /></label>
        <label class="flex items-center gap-2 sm:col-span-2"><input v-model="browse" type="checkbox" :disabled="!!pending || busy" />按约定时长持续浏览</label>
        <template v-if="browse">
          <label>时长（秒）<input v-model.number="duration" type="number" min="1" max="10000" class="mt-1 w-full rounded border p-2" /></label>
          <label>模型词元上限<input v-model.number="maxTokens" type="number" min="1" max="10000000" class="mt-1 w-full rounded border p-2" /></label>
          <label>最低有效观察次数<input v-model.number="minObservations" type="number" min="2" class="mt-1 w-full rounded border p-2" /></label>
          <label>最低有效时间（秒）<input v-model.number="minActive" type="number" min="1" class="mt-1 w-full rounded border p-2" /></label>
          <label>允许停滞上限（秒）<input v-model.number="maxStall" type="number" min="1" max="10800" class="mt-1 w-full rounded border p-2" /></label>
          <p v-if="contractError" class="text-amber-700 sm:col-span-2">{{ contractError }}</p>
        </template>
      </div>
      <div class="chat-columns grid min-h-0 flex-1" :class="{ 'lg:grid-cols-[minmax(0,1fr)_320px]': !workbenchDevice }">
        <section class="flex min-w-0 flex-col bg-white" :class="{ 'min-h-[calc(100dvh-120px)]': !workbenchDevice }" aria-label="任务对话">
          <div v-if="deviceId" class="flex flex-wrap items-center gap-3 border-b border-[#e4ece7] bg-[#f3faf5] px-4 py-2 text-xs text-[#486255] sm:px-8" role="status"><span>{{ manuallyHeld ? '人工接管中，当前不会下发手机任务' : deviceConnection }}</span><button class="text-[#008b47] underline" @click="refreshDeviceConnection">刷新连接</button></div>
          <div ref="conversationPane" class="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-6 sm:px-8 lg:max-h-[calc(100dvh-220px)]" aria-live="polite">
            <div v-if="!transcript.length" class="mx-auto mt-12 max-w-lg text-center">
              <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-[#e8f6ed] text-[#008b47]"><MessageSquare class="h-6 w-6" /></div>
              <h2 class="mt-4 text-lg font-semibold">告诉我需要手机做什么</h2>
              <p class="mt-2 text-sm leading-6 text-[#788a80]">{{ deviceId ? `已选择设备 ${deviceId}，输入任务后请核对安排。` : '直接输入任务，我会在信息不完整时向你确认。' }}</p>
            </div>
            <div v-for="entry in transcript" :key="entry.id" :class="entry.role === 'user' ? 'flex justify-end' : 'flex justify-start gap-2'">
              <div v-if="entry.role === 'assistant'" class="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#e5f5ea] text-[#008b47]"><Bot class="h-4 w-4" /></div>
              <div class="max-w-[min(100%,680px)] space-y-2">
                <p :class="entry.role === 'user' ? 'rounded-2xl rounded-tr-sm bg-[#ddf5e5] px-4 py-3 text-sm leading-6' : 'rounded-2xl rounded-tl-sm bg-[#f1f5f2] px-4 py-3 text-sm leading-6'" class="whitespace-pre-wrap break-words">{{ entry.text }}</p>
                <div v-if="entry.selectedLabel" class="rounded-xl border border-[#dce8df] bg-white p-3 text-sm"><span class="font-medium">选择设备或分组</span><p class="mt-2 text-xs text-[#008b47]">已选择：{{ entry.selectedLabel }}</p></div>
                <template v-if="entry.jobId">
                  <div v-if="deviceJobs.find(job => job.id === entry.jobId)" class="rounded-xl border border-[#dce8df] bg-white p-4 text-sm">
                    <template v-for="job in deviceJobs.filter(item => item.id === entry.jobId)" :key="job.id">
                      <div class="flex items-center justify-between gap-2"><strong>{{ job.goal }}</strong><span class="rounded-full px-2 py-1 text-xs" :class="manualSkip(job) ? 'bg-amber-50 text-amber-800' : 'bg-[#e8f7ec] text-[#008b47]'">{{ manualSkip(job) ? '未下发' : stateText[job.state] || job.state }}</span></div>
                      <p class="mt-2 text-xs text-[#71857a]">{{ scheduleText(job.schedule) }} · 任务 #{{ job.id }}</p>
                      <p v-for="device in job.devices.filter(d => !workbenchDevice || d.domain === deviceId)" :key="device.domain || 'revoked'" class="mt-2 text-xs">{{ device.domain || '已撤销访问' }}：{{ device.active_task ? (device.active_task.cancel_requested ? '停止待确认' : (activeStateText[device.active_task.status] || '状态待核对')) : device.last_task ? (reasonText[device.last_task.reason || ''] || stateText[device.last_task.state] || device.last_task.state) : '等待执行' }}<span v-if="device.last_task?.answer"> · {{ device.last_task.answer }}</span><button v-if="workbenchDevice && device.active_task" class="ml-2 text-[#008b47] underline" @click="showReceipt(device.active_task.task_id)">查看并处理</button><a v-else-if="device.active_task && device.domain" class="ml-2 text-[#008b47] underline" :href="toHashHref('/ai/' + device.domain + '?task=' + device.active_task.task_id)">查看并处理</a></p>
                      <div class="mt-3 flex gap-4 text-xs"><button class="text-[#008b47]" @click="toggleRuns(job)">{{ expanded === job.id ? '收起执行记录' : '查看执行记录' }}</button><button v-if="['bk_running','bk_paused'].includes(job.state)" :disabled="busy" class="text-[#697d70]" @click="cancel(job, false)">{{ workbenchDevice && job.devices.length > 1 ? '停止整项计划的后续安排' : '停止后续' }}</button><button v-if="hasActive(job)" :disabled="busy" class="text-red-700" @click="cancel(job, true)">{{ workbenchDevice && job.devices.length > 1 ? '停止整项计划的当前执行' : '停止当前任务' }}</button></div>
                      <ul v-if="expanded === job.id" class="mt-3 space-y-1 border-t pt-2 text-xs text-[#71857a]"><li v-for="run in runs.filter(r => !workbenchDevice || r.device === deviceId)" :key="run.task_id">{{ showTime(run.scheduled_at) }} · {{ run.device }} · {{ reasonText[run.reason || ''] || stateText[run.state] || run.state }}<span v-if="run.answer">：{{ run.answer }}</span><button v-if="workbenchDevice && run.task_id && !['manual_control','device_busy','device_offline','access_revoked','missed_schedule'].includes(run.reason || '')" class="ml-2 text-[#008b47] underline" @click="emit('viewEvidence', run.task_id)">查看本次证据</button></li></ul>
                    </template>
                  </div><p v-else class="text-xs text-[#72847a]">任务 #{{ entry.jobId }} 的状态暂未取得，请在右侧任务记录查看。</p>
                </template>
              </div>
            </div>
            <div v-if="showTargetChoices && !deviceId" class="ml-10 max-w-[680px] rounded-xl border border-[#d8e6dc] bg-white p-4">
              <div class="flex items-center gap-2 font-semibold"><Smartphone class="h-4 w-4 text-[#008b47]" />选择设备或分组</div>
              <div class="mt-3 flex gap-5 border-b border-[#e5ece7] text-sm"><button :class="targetTab === 'device' ? 'border-b-2 border-[#00964c] pb-2 font-medium text-[#008b47]' : 'pb-2 text-[#72847a]'" @click="targetTab = 'device'; selectedTarget = null">设备</button><button :class="targetTab === 'group' ? 'border-b-2 border-[#00964c] pb-2 font-medium text-[#008b47]' : 'pb-2 text-[#72847a]'" @click="targetTab = 'group'; selectedTarget = null">分组</button></div>
              <div v-if="targetTab === 'device'" class="mt-2 max-h-48 space-y-1 overflow-y-auto"><p v-if="!targets.length" class="py-3 text-xs text-[#72847a]">暂无可选设备，也可以直接输入设备名称。</p><button v-for="device in targets" :key="device.domain" class="flex w-full items-center gap-2 rounded-lg p-2 text-left text-sm" :class="selectedTarget?.name === device.domain ? 'bg-[#e7f7eb]' : 'hover:bg-[#f4f8f5]'" @click="selectedTarget = {kind:'device',name:device.domain,label:device.name + '（' + device.domain + '）'}"><Smartphone class="h-4 w-4 text-[#5d7668]" /><span class="flex-1 truncate">{{ device.name }} <span class="text-xs text-[#8a9b90]">{{ device.domain }}</span></span><span class="text-xs" :class="device.state === 'online' ? 'text-[#008b47]' : 'text-[#a77446]'">{{ device.state === 'online' ? '在线' : '离线' }}</span></button></div>
              <div v-else class="mt-2 max-h-48 space-y-1 overflow-y-auto"><p v-if="!groups.length" class="py-3 text-xs text-[#72847a]">暂无可选分组。</p><button v-for="group in groups" :key="group.id" :disabled="!group.count" class="flex w-full items-center gap-2 rounded-lg p-2 text-left text-sm disabled:opacity-40" :class="selectedTarget?.name === group.name ? 'bg-[#e7f7eb]' : 'hover:bg-[#f4f8f5]'" @click="selectedTarget = {kind:'group',name:group.name,label:group.name}"><Users class="h-4 w-4 text-[#5d7668]" /><span class="flex-1">{{ group.name }}</span><span class="text-xs text-[#72847a]">{{ group.count }} 台</span></button></div>
              <div class="mt-3 flex justify-end"><button :disabled="!selectedTarget || busy" class="rounded-lg bg-[#00964c] px-4 py-2 text-xs font-medium text-white disabled:opacity-40" @click="chooseTarget">确认选择</button></div>
            </div>
            <div v-if="plan || pending" class="ml-10 max-w-[680px] rounded-xl border border-[#d8e6dc] bg-white p-4 text-sm">
              <div class="flex items-center gap-2 font-semibold"><ClipboardList class="h-4 w-4 text-[#008b47]" />核对任务安排</div><p class="mt-3 whitespace-pre-wrap">{{ (pending?.plan || plan)?.goal }}</p><p class="mt-2 text-xs text-[#71857a]">设备：{{ (pending?.plan || plan)?.devices.join('、') }}</p><p class="mt-1 text-xs text-[#71857a]">时间：{{ scheduleText((pending?.plan || plan)!.schedule) }}</p><p v-if="(pending?.plan || plan)?.execution" class="mt-1 text-xs text-[#71857a]">时长：{{ (pending?.plan || plan)?.execution?.duration_s }} 秒；预算：{{ (pending?.plan || plan)?.execution?.budget.max_tokens }} 词元</p><p class="mt-2 text-xs text-[#8a9b90]">设备按当前选择固定；离线或错过时不自动补跑。</p><button class="mt-3 rounded-lg bg-[#00964c] px-4 py-2 text-xs font-medium text-white disabled:opacity-40" :disabled="busy" @click="submit">{{ pending ? '继续核对原任务' : '确认安排' }}</button>
            </div>
            <p v-if="busy" class="ml-10 text-xs text-[#72847a]">正在处理，请稍候…</p><p v-if="error" role="alert" class="ml-10 text-xs text-amber-700">{{ error }}</p>
          </div>
          <form class="border-t border-[#e4ece7] bg-white p-4 sm:px-8" @submit.prevent="prepare()"><div class="flex items-end gap-2 rounded-xl border border-[#d6e3da] bg-white p-2 shadow-sm"><textarea v-model="message" :disabled="!!pending || busy" rows="2" maxlength="4000" class="min-h-[46px] min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none" :placeholder="deviceId ? '输入这台手机的任务，例如：明天上午 9 点检查状态…' : '输入消息，例如：明天上午 9 点让一组手机检查状态…'" @keydown.enter.exact.prevent="prepare()" /><button type="submit" :disabled="busy || !!pending || !modelId || !message.trim() || !!contractError" class="flex items-center gap-1.5 rounded-lg bg-[#00964c] px-4 py-2.5 text-sm font-medium text-white disabled:opacity-40"><Send class="h-4 w-4" />发送</button></div><p v-if="!supportedModels.length" class="mt-2 text-xs text-amber-700">请先在模型管理中配置服务器模型。</p><p v-else-if="!modelId" class="mt-2 text-xs text-amber-700">请先在“任务设置”里选择理解任务的模型。</p><p v-else-if="contractError" class="mt-2 text-xs text-amber-700">请在“任务设置”里补全浏览时长和预算。</p><p v-else class="mt-2 text-xs text-[#a0aea5]">对话保存在当前浏览器；任务记录保存在服务器。</p></form>
        </section>
        <aside class="border-t border-[#e4ece7] bg-[#fbfdfb] px-5 py-5 lg:border-l lg:border-t-0" aria-label="任务记录"><div class="flex items-center justify-between"><h2 class="flex items-center gap-2 font-semibold"><ClipboardList class="h-4 w-4" />任务记录</h2><button class="text-[#008b47]" aria-label="刷新任务记录" @click="refresh(); refreshReceipts()"><RefreshCw class="h-4 w-4" /></button></div><p v-if="targetJobError" class="mt-4 text-xs text-amber-700">{{ targetJobError }}</p><p v-if="!deviceJobs.length && !deviceReceipts.length" class="mt-5 text-sm text-[#91a098]">安排任务后，记录会显示在这里。</p><p v-if="deviceReceiptError" role="alert" class="mt-3 text-xs text-amber-700">{{ deviceReceiptError }}</p><p v-if="deviceId && deviceJobs.length" class="mt-4 text-xs text-[#72847a]">智能任务安排</p><button v-for="job in deviceJobs" :key="job.id" class="mt-3 w-full rounded-lg border border-[#e4eee7] bg-white p-3 text-left hover:border-[#a9d6b8]" @click="addMessage('assistant', '这是任务的当前状态。', job.id)"><div class="flex items-start justify-between gap-2"><span class="truncate text-sm font-medium">{{ job.goal }}</span><span class="shrink-0 text-xs" :class="manualSkip(job) ? 'text-amber-800' : 'text-[#008b47]'">{{ manualSkip(job) ? '未下发' : stateText[job.state] || job.state }}</span></div><p class="mt-2 text-xs text-[#8a9b90]">{{ scheduleText(job.schedule) }} · #{{ job.id }}</p><p v-if="String(job.id) === targetJob" class="mt-1 text-xs text-[#008b47]">从任务列表打开的任务 #{{ job.id }}</p></button><p v-if="deviceId && deviceReceipts.length" class="mt-5 text-xs text-[#72847a]">本机任务记录</p><button v-for="item in deviceReceipts" :key="item.task_id" class="mt-3 w-full rounded-lg border border-[#e4eee7] bg-white p-3 text-left hover:border-[#a9d6b8]" @click="showReceipt(item.task_id)"><span class="block truncate text-sm font-medium">{{ item.goal }}</span><span class="mt-2 block text-xs text-[#008b47]">{{ item.cancel_requested && !['done','failed','cancelled','rejected'].includes(item.status) ? '停止待确认' : receiptLabels[item.status] || item.status }}</span></button></aside>
      </div>
    </main>
    <section v-if="workbenchDevice && receiptOpen" class="min-w-0 flex-1 overflow-auto bg-white p-4" aria-label="本机任务详情">
      <button type="button" class="mb-3 rounded border px-3 py-1.5 text-sm" @click="receiptOpen = false; refreshReceipts()">返回任务对话</button>
      <AiTasksPage v-if="active" :key="selectedReceipt" embedded workbench :device-id="deviceId" :task-id="selectedReceipt" @view-evidence="emit('viewEvidence', $event)" />
    </section>
    <Dialog v-if="!workbenchDevice" v-model:open="receiptOpen"><DialogContent class="max-h-[90dvh] overflow-y-auto sm:max-w-[800px]"><DialogHeader><DialogTitle>本机任务记录 · {{ deviceId }}</DialogTitle></DialogHeader><AiTasksPage v-if="receiptOpen" :key="selectedReceipt" embedded :device-id="deviceId" :task-id="selectedReceipt" /></DialogContent></Dialog>
  </div>
</template>

<style scoped>
.embedded-chat { height: 100%; min-height: 300px; container-type: inline-size; }
.embedded-chat .chat-columns { grid-template-columns: minmax(0, 1fr) minmax(180px, 26%); }
.embedded-chat .chat-columns > section { min-height: 0; }
.embedded-chat aside { min-width: 0; overflow-y: auto; }
.embedded-chat [aria-live="polite"] { max-height: none; }
@container (max-width: 580px) {
  .embedded-chat .chat-columns { display: flex; flex-direction: column; overflow-y: auto; }
  .embedded-chat .chat-columns > section { min-height: 340px; flex: 1 0 65%; }
  .embedded-chat aside { flex-shrink: 0; }
}
</style>
