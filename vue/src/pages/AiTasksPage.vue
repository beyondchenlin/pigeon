<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { apiRequest, formatApiError, ApiRequestError } from '@/lib/api';
import { useHashPathname, toHashHref } from '@/lib/hash-router';
import { prepareTask, dispatchTask, terminalTask, taskProgress, stoppingTask, mergeTaskReceipt, mergeTaskReceipts, validateExecution, supervisionSeconds, type BrowseExecution } from '@/lib/task-request.mjs';

type EndEvidence = { state?: string; evidence_id?: string; task_id?: string; run_id?: string; display_id?: number; captured_at_ms?: number; source?: string; reason?: string; capture_timing?: string; late_capture?: boolean };
type ExecutionResult = { duration_reached?: boolean; business_success?: boolean; execution_finished?: boolean;
  actual_started_at_ms?: number; finished_at_ms?: number; last_progress_at_ms?: number; elapsed_ms?: number; active_ms?: number; waiting_ms?: number; paused_ms?: number; stalled_ms?: number;
  observations?: number; usage?: unknown; reason?: string; evidence?: EndEvidence };
type Receipt = { task_id: string; request_id: string; goal: string; status: string; error?: string; cancel_requested?: boolean;
  question_key?: string; answer_submission?: {request_id: string; status: string};
  created_at: number; deadline?: number; updated_at?: number; execution?: BrowseExecution; phone_received_at?: number; source_event_at_ms?: number | null; stale?: boolean; stop_confirmation_overdue?: boolean;
  result?: { run_id?: string; progress?: unknown; step?: unknown; answer?: string; reason?: string; message?: string; note?: string; prompt?: string; question?: string; options?: string[]; answerable?: boolean; verified?: boolean; execution_result?: ExecutionResult } };
type Pending = { taskId: string; requestId: string };
const path = useHashPathname();
// App mounts this page with the device path as its key. Freeze it for in-flight calls.
const domain = ref(path.value.split('/')[2] || '');
const goal = ref('');
const timeoutSeconds = ref(900);
const browseMode = ref(false);
const durationSeconds = ref(7200);
const progressInterval = ref(600);
const eventLatency = ref(30);
const finishTimeout = ref(60);
const stopTimeout = ref(30);
const maxTokens = ref<number | ''>('');
const minObservations = ref<number | ''>('');
const minActiveSeconds = ref<number | ''>('');
const maxStallSeconds = ref<number | ''>('');
const executionDraft = computed(() => ({ version:1, kind:'browse', clock:'elapsed',
  duration_s:durationSeconds.value, finish_timeout_s:finishTimeout.value, stop_timeout_s:stopTimeout.value,
  progress_interval_s:progressInterval.value, event_latency_s:eventLatency.value,
  budget:{max_tokens:maxTokens.value}, progress_requirements:{min_observations:minObservations.value,
    min_active_s:minActiveSeconds.value,max_stall_s:maxStallSeconds.value} }));
const contractError = computed(() => {
  if (!browseMode.value) return '';
  try { validateExecution(executionDraft.value); return ''; }
  catch (e) { return e instanceof Error ? e.message : '请填写完整的浏览执行约定。'; }
});
const supervision = computed(() => !contractError.value && browseMode.value ? supervisionSeconds(validateExecution(executionDraft.value)) : 0);
const validTimeout = computed(() => Number.isInteger(timeoutSeconds.value) && timeoutSeconds.value >= 15 && timeoutSeconds.value <= 10800);
const progressFor = (task: Receipt) => taskProgress(task, !!error.value);
const formatTime = (seconds?: number | null) => typeof seconds === 'number' && Number.isFinite(seconds) ? new Date(seconds * 1000).toLocaleString() : '未提供';
const formatMillis = (millis?: number | null) => typeof millis === 'number' && Number.isFinite(millis) ? formatTime(millis / 1000) : '未提供';
const duration = (millis?: number) => typeof millis === 'number' && Number.isFinite(millis) ? Math.floor(millis / 1000) + '秒' : '未提供';
const usageText = (usage: unknown) => {
  if (!usage || typeof usage !== 'object') return usage === undefined ? '未提供' : String(usage);
  const values = usage as Record<string, unknown>;
  const names: Record<string,string> = {reported_tokens:'已报告词元',input_tokens:'输入词元',output_tokens:'输出词元',model_calls:'模型调用次数',unreported_calls:'未报告用量的调用'};
  return Object.entries(values).map(([key,value]) => (names[key] || key) + '：' + String(value)).join(' · ');
};
const reasonText = (reason?: string) => ({duration_elapsed:'业务期限已到',user_stopped:'用户停止',stalled:'超过允许停滞时间',
  ended_early:'执行提前结束',budget_exhausted:'模型预算耗尽',finish_timeout:'到期收尾超时'} as Record<string,string>)[reason || ''] || taskErrorText(reason || '尚未提供');
const taskErrorText = (error: string) => {
  // The phone can prefix its budget failure with the surrounding model error.
  const code = /^(?:模型调用异常:)?model_usage_unreported(?::|$)/.test(error) ? 'model_usage_unreported' : error;
  return ({
  unsupported_execution_contract:'手机版本不支持本次执行约定，请核对候选包版本。',
  execution_response_mismatch:'手机返回的执行约定不一致，已请求停止并继续核对。',
  unconfirmed_phone_terminal:'手机终态证据不完整，仍保持占用并继续核对。',
  capability_auth_refused:'手机能力查询未通过认证，请核对连接。',
  capability_check_unavailable:'尚未取得手机能力信息，本次任务未派发。',
  connection_unknown:'连接状态待核对，保留原任务编号继续查询。',
  delivery_unknown:'请求是否送达待核对，保留原任务编号继续查询。',
  supervision_expired:'服务器监督期限已到，已请求停止，等待手机确认。',
  model_usage_unreported:'模型未报告本次用量，无法核对预算。',
  budget_exhausted:'模型用量已达到本次预算，任务无法继续执行。',
  actual_task_display_unavailable:'本次未取得可用于结束证据的实际执行画面。'
  } as Record<string,string>)[code] || error;
};
const taskDiagnostics = (task: Receipt) => [
  ['任务错误', task.error], ['手机结束原因', task.result?.reason],
  ['执行结束原因', task.result?.execution_result?.reason], ['截图缺失原因', task.result?.execution_result?.evidence?.reason]
].filter(([, raw]) => raw && taskErrorText(raw) !== raw).map(([name, raw]) => name + '：' + raw).join('\n');
const validEndEvidence = (task: Receipt) => {
  const evidence = task.result?.execution_result?.evidence;
  const result = task.result?.execution_result;
  return terminalTask(task.status) && result?.execution_finished === true && evidence?.state === 'captured' &&
    typeof result.actual_started_at_ms === 'number' && typeof result.finished_at_ms === 'number' &&
    typeof evidence.captured_at_ms === 'number' && evidence.captured_at_ms >= result.actual_started_at_ms && evidence.captured_at_ms <= result.finished_at_ms &&
    evidence.task_id === task.task_id && typeof evidence.run_id === 'string' && !!evidence.run_id && evidence.run_id === task.result?.run_id &&
    !!evidence.evidence_id && typeof evidence.display_id === 'number' && Number.isInteger(evidence.display_id) && evidence.display_id >= 0 && Number.isFinite(evidence.captured_at_ms) &&
    evidence.source === 'bk_task_display' && evidence.capture_timing === 'terminal_before_release' && evidence.late_capture === false;
};
const receipts = ref<Receipt[]>([]);
const pending = ref<Pending | null>(null);
const busy = ref(false);
const loading = ref(false);
const loaded = ref(false);
const error = ref('');
const submitError = ref('');
const answerDrafts = ref<Record<string,string>>({});
const answerAttempts = ref<Record<string,boolean>>({});
const answerError = ref('');
const stopRequests = ref<Record<string, boolean>>({});
const stopKey = (task: Receipt) => `bk-task-stop:${domain.value}:${task.task_id}`;
const isStopping = (task: Receipt) => !terminalTask(task.status) && (stoppingTask(task) || !!stopRequests.value[task.task_id]);
const taskName = (task: Receipt) => isStopping(task) ? '停止待确认' : names[task.status] || task.status;
const answerKey = (task: Receipt) => `bk-answer:${domain.value}:${task.task_id}:${task.question_key}`;
const answerAttempted = (task: Receipt) => !!task.answer_submission || !!answerAttempts.value[answerKey(task)];
const key = computed(() => `bk-task-pending:${domain.value}`);
const active = computed(() => receipts.value.some(r => !terminalTask(r.status)));
const disabled = computed(() => busy.value || !loaded.value || active.value || !!pending.value || !goal.value.trim() ||
  (browseMode.value ? !!contractError.value : !validTimeout.value));
const names: Record<string,string> = { submitting:'正在下达', starting:'正在启动', queued:'排队中', running:'手机执行中', paused:'已暂停',
  waiting_for_answer:'需要补充信息', waiting_on_phone:'需要手机处理', unknown:'正在核对执行状态', finishing:'手机收尾中', finish_overdue:'收尾超期，仍在核对', stopping:'停止中', stop_unconfirmed:'停止待确认', done:'执行结束', failed:'未完成', cancelled:'已停止', rejected:'未开始' };
let timer: ReturnType<typeof setInterval>;
let mounted = true;

function acceptReceipt(task: Receipt) {
  const previous = receipts.value.find(row => row.task_id === task.task_id);
  const merged = mergeTaskReceipt(previous, task);
  receipts.value = [merged, ...receipts.value.filter(row => row.task_id !== task.task_id)];
  if (terminalTask(merged.status)) {
    delete stopRequests.value[merged.task_id];
    sessionStorage.removeItem(stopKey(merged));
    if (pending.value?.taskId === merged.task_id) {
      sessionStorage.removeItem(key.value); pending.value = null;
    }
  }
}

async function refresh(manual = false) {
  if (!mounted || loading.value) return;
  loading.value = true;
  try {
    const reply = await apiRequest<{tasks: Receipt[]}>(`/api/v1/device/${domain.value}/ai/tasks`, {cache:'no-store',signal:AbortSignal.timeout(45000)});
    if (!mounted) return;
    if (!reply.data?.tasks) throw new Error('未取得任务记录');
    receipts.value = mergeTaskReceipts(receipts.value, reply.data.tasks);
    for (const task of receipts.value) {
      if (task.question_key && sessionStorage.getItem(answerKey(task))) answerAttempts.value[answerKey(task)] = true;
      if (terminalTask(task.status)) { delete stopRequests.value[task.task_id]; sessionStorage.removeItem(stopKey(task)); }
      else if (sessionStorage.getItem(stopKey(task))) stopRequests.value[task.task_id] = true;
    }
    loaded.value = true;
    if (pending.value) {
      const p = pending.value;
      const state = await apiRequest<Receipt>(`/api/v1/device/${domain.value}/ai/tasks/${p.taskId}${manual ? '?refresh=1' : '?refresh=0'}`, {cache:'no-store',signal:AbortSignal.timeout(45000)});
      if (!mounted) return;
      if (state.data) acceptReceipt(state.data);
    }
    if (manual) {
      for (const task of receipts.value.filter(row => !terminalTask(row.status) && row.task_id !== pending.value?.taskId)) {
        const state = await apiRequest<Receipt>(`/api/v1/device/${domain.value}/ai/tasks/${task.task_id}?refresh=1`, {cache:'no-store',signal:AbortSignal.timeout(45000)});
        if (!mounted) return;
        if (state.data) acceptReceipt(state.data);
      }
    }
    error.value = '';
  } catch (e) {
    if (!mounted) return;
    error.value = pending.value ? '正在查询原任务。连接不确定时不会重新下达，也不会重复发布或发消息。' : formatApiError(e, '暂时无法读取任务');
  } finally { loading.value = false; }
}

async function submit() {
  if (disabled.value) return;
  busy.value = true; submitError.value = '';
  try {
    const options = browseMode.value ? {execution:validateExecution(executionDraft.value)} : {timeout_s:timeoutSeconds.value};
    const request = await prepareTask(domain.value, goal.value, undefined, options);
    const reply = await dispatchTask(request, (p: Pending) => {
      // If local persistence fails, nothing is sent. No goal or credential is stored here.
      sessionStorage.setItem(key.value, JSON.stringify(p)); pending.value = p;
    }, (body: unknown) => apiRequest<Receipt>(`/api/v1/device/${domain.value}/ai/tasks`, {
      method:'POST', headers:{'X-Requested-With':'BK-Task'}, body:JSON.stringify(body), signal:AbortSignal.timeout(45000),
    }));
    if (!mounted) return;
    if (reply.data) acceptReceipt(reply.data);
    goal.value = '';
  } catch (e) {
    // Only the API's explicit pre-admission error codes prove no task was accepted.
    if (!mounted) return;
    if (e instanceof ApiRequestError && ['AI_ORIGIN','AI_REQUEST','AI_BODY','AI_FIELDS','AI_ARGUMENT','AI_TIMEOUT','AI_EXECUTION','AI_CONFIG','AI_OFFLINE','AI_BUSY','AI_REQUEST_REUSED','A1001','D3001'].includes(e.code || '')) {
      sessionStorage.removeItem(key.value); pending.value = null;
    }
    submitError.value = formatApiError(e, '下达结果暂时不确定，正在查询原任务。');
  } finally { busy.value = false; }
}

async function cancel(task: Receipt) {
  if (busy.value || terminalTask(task.status)) return;
  busy.value = true;
  stopRequests.value[task.task_id] = true;
  // Show intent before waiting for transport; acceptance never means the phone stopped.
  try { sessionStorage.setItem(stopKey(task), 'requested'); } catch { /* Server receipt remains authoritative. */ }
  try {
    const reply = await apiRequest<Receipt>(`/api/v1/device/${domain.value}/ai/tasks/${task.task_id}/cancel`, {
      method:'POST', headers:{'X-Requested-With':'BK-Task'}, body:'{}', signal:AbortSignal.timeout(45000),
    });
    if (!mounted) return;
    if (reply.data?.task_id === task.task_id) acceptReceipt(reply.data);
    await refresh();
  } catch (e) { if (mounted) error.value = formatApiError(e, '停止状态待核对'); }
  finally { busy.value = false; }
}

async function answer(task: Receipt) {
  const text = (answerDrafts.value[task.task_id] || '').trim();
  if (busy.value || !text || !task.question_key || task.status !== 'waiting_for_answer' || isStopping(task) || answerAttempted(task)) return;
  busy.value = true; answerError.value = '';
  const storageKey = answerKey(task);
  try {
    const requestId = crypto.randomUUID();
    sessionStorage.setItem(storageKey, requestId); // Receipt first; no reply text or automatic replay.
    answerAttempts.value[storageKey] = true;
    await apiRequest(`/api/v1/device/${domain.value}/ai/tasks/${task.task_id}/answer`, {
      method:'POST', headers:{'X-Requested-With':'BK-Task'}, signal:AbortSignal.timeout(45000),
      body:JSON.stringify({text, question_key:task.question_key, request_id:requestId}),
    });
    if (!mounted) return;
    answerDrafts.value[task.task_id] = '';
  } catch (e) {
    if (!mounted) return;
    if (e instanceof ApiRequestError && ['AI_ANSWER_ARGUMENT','AI_ANSWER_BUSY','AI_QUESTION_CLOSED','AI_ORIGIN','AI_REQUEST','AUTH'].includes(e.code || '')) {
      sessionStorage.removeItem(storageKey); delete answerAttempts.value[storageKey];
    }
    answerError.value = formatApiError(e, '答复是否送达仍在核对。不会重复发送，请查看原任务状态。');
  } finally { busy.value = false; await refresh(); }
}
onMounted(() => {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key.value) || 'null');
    if (saved && /^[a-f0-9-]{36}$/.test(saved.taskId) && /^[a-f0-9-]{36}$/.test(saved.requestId)) pending.value = saved;
  } catch { /* An unreadable local receipt never causes automatic submission. */ }
  void refresh(); timer = setInterval(() => { void refresh(); }, 5000);
});
onBeforeUnmount(() => { mounted = false; clearInterval(timer); });
</script>

<template>
  <main class="mx-auto max-w-4xl space-y-6 p-4 sm:p-6">
    <header class="flex flex-wrap items-start justify-between gap-4">
      <div><a :href="toHashHref('/devices/' + domain)" class="text-sm underline">返回设备详情</a>
        <h1 class="mt-3 text-2xl font-semibold">让手机完成任务</h1></div>
      <a :href="toHashHref('/evidence/' + domain)" class="rounded border px-4 py-2 text-sm">查看截图与归档</a>
    </header>
    <form class="space-y-3 rounded-lg border bg-card p-4" @submit.prevent="submit">
      <label for="task-goal" class="block font-medium">这次需要做什么？</label>
      <textarea id="task-goal" v-model="goal" rows="4" maxlength="4000" class="w-full resize-y rounded-md border bg-background p-3 text-base"
        placeholder="例如：打开抖音浏览视频，只查看，不点赞、评论、关注、发送消息或发布内容。" />
      <p class="text-sm text-muted-foreground">手机使用自己的模型执行。持续浏览需要填写执行时长、用量预算和有效进展要求。</p>
      <label class="flex items-center gap-2 font-medium"><input v-model="browseMode" type="checkbox" /> 按时长持续浏览</label>
      <div v-if="!browseMode" class="space-y-2">
        <label for="task-timeout" class="block text-sm font-medium">任务超时上限（秒）</label>
        <input id="task-timeout" v-model.number="timeoutSeconds" type="number" min="15" max="10800" step="1" required aria-describedby="task-timeout-help" class="w-40 rounded border bg-background px-3 py-2" />
        <p id="task-timeout-help" class="text-sm text-muted-foreground">默认900秒。此项是服务器监督上限；持续浏览请启用上方选项。到达监督上限会请求停止，仍需核对手机实际终态。</p>
      </div>
      <fieldset v-if="browseMode" class="space-y-4 rounded border p-4">
        <legend class="px-1 font-medium">本次浏览执行约定</legend>
        <p class="text-sm text-muted-foreground">从手机实际开始按自然经过时间计时；等待、暂停和断线不自动延长。以下时限可修改，用量和进展要求须自行填写。</p>
        <div class="grid gap-4 sm:grid-cols-2">
          <label class="space-y-1 text-sm">目标浏览时长（秒）<input id="browse-duration" v-model.number="durationSeconds" type="number" min="1" max="10000" step="1" required class="block w-full rounded border bg-background px-3 py-2" /></label>
          <label class="space-y-1 text-sm">模型词元用量上限<input id="browse-budget" v-model.number="maxTokens" type="number" min="1" max="10000000" step="1" required placeholder="填写本次允许的最大词元数" class="block w-full rounded border bg-background px-3 py-2" /></label>
          <label class="space-y-1 text-sm">最低有效观察次数<input id="browse-observations" v-model.number="minObservations" type="number" min="2" max="1000000" step="1" required placeholder="至少2次" class="block w-full rounded border bg-background px-3 py-2" /></label>
          <label class="space-y-1 text-sm">最低有效执行时间（秒）<input id="browse-active" v-model.number="minActiveSeconds" type="number" min="1" :max="durationSeconds" step="1" required class="block w-full rounded border bg-background px-3 py-2" /></label>
          <label class="space-y-1 text-sm">最长允许停滞（秒）<input id="browse-stall" v-model.number="maxStallSeconds" type="number" min="1" max="10800" step="1" required class="block w-full rounded border bg-background px-3 py-2" /></label>
        </div>
        <p class="text-sm text-muted-foreground">有效观察和执行依据手机的画面与执行记录判定；滑动次数或停留时间不能单独证明完成。</p>
        <details class="space-y-3">
          <summary class="cursor-pointer text-sm font-medium">进度、通知与收尾时限</summary>
          <div class="grid gap-4 pt-3 sm:grid-cols-2">
            <label class="space-y-1 text-sm">普通进度间隔（秒）<input id="browse-progress" v-model.number="progressInterval" type="number" min="1" max="3600" step="1" required class="block w-full rounded border bg-background px-3 py-2" /></label>
            <label class="space-y-1 text-sm">异常与结束通知目标（秒）<input id="browse-latency" v-model.number="eventLatency" type="number" min="1" max="30" step="1" required class="block w-full rounded border bg-background px-3 py-2" /></label>
            <label class="space-y-1 text-sm">到期收尾上限（秒）<input id="browse-finish" v-model.number="finishTimeout" type="number" min="1" max="120" step="1" required class="block w-full rounded border bg-background px-3 py-2" /></label>
            <label class="space-y-1 text-sm">停止确认目标（秒）<input id="browse-stop" v-model.number="stopTimeout" type="number" min="1" max="120" step="1" required class="block w-full rounded border bg-background px-3 py-2" /></label>
          </div>
          <p class="text-sm text-muted-foreground">通知与停止时限是本次配置目标。未收到手机确认时继续显示待确认，不解除占用。</p>
        </details>
        <p v-if="contractError" class="text-sm text-amber-700">{{ contractError }}</p>
        <p v-else class="text-sm text-muted-foreground">服务器监督时长：{{ supervision }}秒，包含120秒启动预留、执行、收尾及通知与停止确认预留。手机端仍按{{ durationSeconds }}秒业务期限执行。</p>
      </fieldset>
      <button type="submit" :disabled="disabled" class="rounded-md bg-primary px-5 py-2 text-primary-foreground disabled:opacity-50">{{ busy ? '处理中…' : '交给这台手机' }}</button>
      <span v-if="active || pending" class="ml-3 text-sm text-muted-foreground">本台手机有任务正在执行或核对中</span>
    </form>
    <p v-if="pending && !receipts.some(task => task.task_id === pending?.taskId)" role="status" class="rounded border border-amber-500 p-3 text-sm">原任务受理状态待核对。正在查回原请求，不会创建替代任务。任务编号：{{ pending.taskId }}</p>
    <p v-if="error" role="alert" class="rounded border border-amber-500 p-3 text-sm">{{ error }}</p>
    <p v-if="submitError" role="alert" class="rounded border border-amber-500 p-3 text-sm">{{ submitError }}</p>
    <p v-if="answerError" role="alert" class="rounded border border-amber-500 p-3 text-sm">{{ answerError }}</p>
    <section aria-label="任务记录" class="space-y-4">
      <div class="flex items-center justify-between"><h2 class="text-lg font-semibold">任务记录</h2>
        <button :disabled="loading" class="rounded border px-3 py-1 text-sm" @click="refresh(true)">刷新状态</button></div>
      <p v-if="loaded && !receipts.length" class="rounded border p-6 text-sm text-muted-foreground">还没有任务。输入本次需求即可开始。</p>
      <article v-for="task in receipts" :key="task.task_id" class="space-y-3 rounded-lg border bg-card p-4">
        <div class="flex flex-wrap justify-between gap-2"><strong>{{ taskName(task) }}</strong>
          <time class="text-sm text-muted-foreground">{{ new Date(task.created_at * 1000).toLocaleString() }}</time></div>
        <p class="whitespace-pre-wrap break-words">{{ task.goal }}</p>
        <div class="space-y-1 text-sm text-muted-foreground">
          <p>服务器监督截止：{{ formatTime(task.deadline) }}（浏览器本地时间）</p>
          <p v-if="task.execution">浏览目标：{{ task.execution.duration_s }}秒；从手机实际开始按自然经过时间计时，等待与暂停不延长。</p>
          <p>手机事件发生：{{ formatMillis(task.source_event_at_ms) }} · 服务器收到：{{ formatTime(task.phone_received_at) }}</p>
          <p v-if="progressFor(task).progress || progressFor(task).step" class="font-medium">{{ progressFor(task).stale ? '上次记录，非实时进度' : terminalTask(task.status) ? '终态进度记录' : '最近一次手机进度快照' }}</p>
          <p v-if="progressFor(task).progress" class="whitespace-pre-wrap break-words">进度：{{ progressFor(task).progress }}</p>
          <p v-if="progressFor(task).step" class="whitespace-pre-wrap break-words">步骤：{{ progressFor(task).step }}</p>
          <p v-if="progressFor(task).stale">连接或执行状态仍在核对；以下结果可能是旧快照，不代表手机当前状态。</p>
        </div>
        <p v-if="task.result?.answer || task.result?.reason || task.result?.message || task.result?.prompt" class="whitespace-pre-wrap break-words border-l-2 pl-3">{{ task.result?.answer || (task.result?.reason ? taskErrorText(task.result.reason) : task.result?.message || task.result?.prompt) }}</p>
        <p v-if="task.stop_confirmation_overdue" class="text-sm text-amber-700">已超过停止确认目标，尚未确认手机停止；设备继续保持占用。</p>
        <p v-if="isStopping(task)" role="status" class="rounded border border-amber-500 p-3 text-sm">已请求停止，正在核对手机是否实际结束。本台手机仍被占用，确认终态后才能下达新任务。</p>
        <p v-if="task.error" class="text-sm text-amber-700">{{ taskErrorText(task.error) }}</p>
        <p v-if="task.result?.note" class="text-sm text-muted-foreground">{{ task.result.note }}</p>
        <section v-if="task.execution" aria-label="浏览执行结果" class="space-y-2 rounded border p-3 text-sm">
          <h3 class="font-medium">浏览执行结果</h3>
          <template v-if="task.result?.execution_result">
            <p>目标时长：{{ task.result.execution_result.duration_reached === true ? '已到期' : '未确认到期' }} · 业务结果：{{ task.result.execution_result.business_success === true ? '满足进展要求' : task.result.execution_result.execution_finished ? '未满足全部要求' : '尚未结束' }}</p>
            <p>实际开始：{{ formatMillis(task.result.execution_result.actual_started_at_ms) }} · 实际结束：{{ formatMillis(task.result.execution_result.finished_at_ms) }}</p>
            <p>最后有效进展：{{ formatMillis(task.result.execution_result.last_progress_at_ms) }}</p>
            <p>自然经过：{{ duration(task.result.execution_result.elapsed_ms) }} · 有效执行：{{ duration(task.result.execution_result.active_ms) }} · 等待：{{ duration(task.result.execution_result.waiting_ms) }} · 暂停：{{ duration(task.result.execution_result.paused_ms) }} · 停滞：{{ duration(task.result.execution_result.stalled_ms) }}</p>
            <p>有效观察：{{ task.result.execution_result.observations ?? '未提供' }} · 结束原因：{{ reasonText(task.result.execution_result.reason) }}</p>
            <details><summary class="cursor-pointer">手机模型用量</summary><pre class="whitespace-pre-wrap break-words">{{ usageText(task.result.execution_result.usage) }}</pre></details>
          </template>
          <p v-else>等待手机返回执行记录。</p>
          <div v-if="validEndEvidence(task)" class="space-y-1 border-t pt-2">
            <p>结束截图已由手机采集；请在本次证据中核验归档图片。</p>
            <p>设备：{{ domain }} · 任务：{{ task.task_id }} · 执行编号：{{ task.result?.execution_result?.evidence?.run_id }}</p>
            <p>执行显示：{{ task.result?.execution_result?.evidence?.display_id }} · 采集时间：{{ formatMillis(task.result?.execution_result?.evidence?.captured_at_ms) }} · 来源：{{ task.result?.execution_result?.evidence?.source === 'bk_task_display' ? '手机任务执行显示' : task.result?.execution_result?.evidence?.source }}</p>
          </div>
          <p v-else-if="terminalTask(task.status)" class="text-amber-700">缺少已核实归属的真实结束截图，完整浏览验收未通过。{{ taskErrorText(task.result?.execution_result?.evidence?.reason || '手机未提供完整的结束截图记录。') }}</p>
          <p v-else class="text-muted-foreground">结束截图将在手机收尾时采集；尚未取得。</p>
        </section>
        <details v-if="taskDiagnostics(task)" class="text-sm text-muted-foreground"><summary class="cursor-pointer">诊断详情（原始记录）</summary><pre class="whitespace-pre-wrap break-words">{{ taskDiagnostics(task) }}</pre></details>
        <p v-if="task.status === 'done'" class="text-sm text-muted-foreground">执行已结束；实际结果请结合归档截图和复查记录核验。</p>
        <form v-if="task.status === 'waiting_for_answer' && task.result?.answerable && task.question_key && !isStopping(task)" class="space-y-3 rounded border p-3" @submit.prevent="answer(task)">
          <p class="whitespace-pre-wrap break-words font-medium">{{ task.result.question }}</p>
          <template v-if="!answerAttempted(task)">
            <div v-if="task.result.options?.length" class="flex flex-wrap gap-2">
              <button v-for="option in task.result.options" :key="option" type="button" :disabled="busy" class="rounded border px-3 py-2 text-sm" @click="answerDrafts[task.task_id] = option">{{ option }}</button>
            </div>
            <label :for="'task-answer-' + task.task_id" class="block text-sm">补充说明</label>
            <textarea :id="'task-answer-' + task.task_id" v-model="answerDrafts[task.task_id]" maxlength="2000" rows="3" class="w-full rounded border bg-background p-2" />
            <button type="submit" :disabled="busy || !answerDrafts[task.task_id]?.trim()" class="rounded bg-primary px-4 py-2 text-primary-foreground disabled:opacity-50">答复并继续原任务</button>
          </template>
          <p v-else class="text-sm text-muted-foreground">{{ task.answer_submission?.status === 'rejected' ? '答复未被接收。请核对手机当前提示；本次答复不会重发。' : '本问题已有答复提交记录，正在核对后续状态，不会重复发送。' }}</p>
        </form>
        <p v-if="task.status === 'waiting_on_phone'" class="text-sm text-amber-700">这一步需要在手机处理，请查看手机提示；网页不能代替系统授权或身份验证。</p>
        <div class="flex gap-4 text-sm"><a :href="toHashHref('/evidence/' + domain + '?task=' + task.task_id)" class="underline">查看本次证据</a>
          <button v-if="!terminalTask(task.status)" :disabled="busy" class="underline" @click="cancel(task)">{{ isStopping(task) ? '再次请求停止' : '停止任务' }}</button></div>
      </article>
    </section>
  </main>
</template>
