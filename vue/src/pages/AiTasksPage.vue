<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { apiRequest, formatApiError, ApiRequestError } from '@/lib/api';
import { useHashPathname, toHashHref } from '@/lib/hash-router';
import { prepareTask, dispatchTask, terminalTask } from '@/lib/task-request.mjs';

type Receipt = { task_id: string; request_id: string; goal: string; status: string; error?: string;
  created_at: number; result?: { answer?: string; message?: string; note?: string; prompt?: string; verified?: boolean } };
type Pending = { taskId: string; requestId: string };
const path = useHashPathname();
const domain = computed(() => path.value.split('/')[2] || '');
const goal = ref('');
const receipts = ref<Receipt[]>([]);
const pending = ref<Pending | null>(null);
const busy = ref(false);
const loading = ref(false);
const loaded = ref(false);
const error = ref('');
const key = computed(() => `bk-task-pending:${domain.value}`);
const active = computed(() => receipts.value.some(r => !terminalTask(r.status)));
const disabled = computed(() => busy.value || !loaded.value || active.value || !!pending.value || !goal.value.trim());
const names: Record<string,string> = { submitting:'正在下达', starting:'正在启动', queued:'排队中', running:'手机执行中', paused:'已暂停',
  waiting_for_answer:'需要补充信息', waiting_on_phone:'需要手机处理', unknown:'正在核对执行状态', done:'执行结束', failed:'未完成', cancelled:'已停止', rejected:'未开始' };
let timer: ReturnType<typeof setInterval>;

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  try {
    const reply = await apiRequest<{tasks: Receipt[]}>(`/api/v1/device/${domain.value}/ai/tasks`, {cache:'no-store'});
    if (!reply.data?.tasks) throw new Error('未取得任务记录');
    receipts.value = reply.data.tasks;
    loaded.value = true;
    if (pending.value) {
      const p = pending.value;
      const state = await apiRequest<Receipt>(`/api/v1/device/${domain.value}/ai/tasks/${p.taskId}`, {cache:'no-store'});
      if (state.data) {
        receipts.value = [state.data, ...receipts.value.filter(r => r.task_id !== p.taskId)];
        if (terminalTask(state.data.status)) { sessionStorage.removeItem(key.value); pending.value = null; }
      }
    }
    error.value = '';
  } catch (e) {
    error.value = pending.value ? '正在查询原任务。连接不确定时不会重新下达，也不会重复发布或发消息。' : formatApiError(e, '暂时无法读取任务');
  } finally { loading.value = false; }
}

async function submit() {
  if (disabled.value) return;
  busy.value = true; error.value = '';
  try {
    const request = await prepareTask(domain.value, goal.value);
    const reply = await dispatchTask(request, (p: Pending) => {
      // If local persistence fails, nothing is sent. No goal or credential is stored here.
      sessionStorage.setItem(key.value, JSON.stringify(p)); pending.value = p;
    }, (body: unknown) => apiRequest<Receipt>(`/api/v1/device/${domain.value}/ai/tasks`, {
      method:'POST', headers:{'X-Requested-With':'BK-Task'}, body:JSON.stringify(body), signal:AbortSignal.timeout(45000),
    }));
    if (reply.data) receipts.value = [reply.data, ...receipts.value.filter(r => r.task_id !== reply.data.task_id)];
    goal.value = '';
  } catch (e) {
    // Only the API's explicit pre-admission error codes prove no task was accepted.
    if (e instanceof ApiRequestError && ['AI_ORIGIN','AI_REQUEST','AI_BODY','AI_FIELDS','AI_ARGUMENT','AI_TIMEOUT','AI_CONFIG','AI_OFFLINE','AI_BUSY','AI_REQUEST_REUSED','A1001','D3001'].includes(e.code || '')) {
      sessionStorage.removeItem(key.value); pending.value = null;
    }
    error.value = formatApiError(e, '下达结果暂时不确定，正在查询原任务。');
  } finally { busy.value = false; }
}

async function cancel(task: Receipt) {
  if (busy.value) return;
  busy.value = true;
  try {
    await apiRequest(`/api/v1/device/${domain.value}/ai/tasks/${task.task_id}/cancel`, {
      method:'POST', headers:{'X-Requested-With':'BK-Task'}, body:'{}',
    });
    await refresh();
  } catch (e) { error.value = formatApiError(e, '停止状态待核对'); }
  finally { busy.value = false; }
}
onMounted(() => {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key.value) || 'null');
    if (saved && /^[a-f0-9-]{36}$/.test(saved.taskId) && /^[a-f0-9-]{36}$/.test(saved.requestId)) pending.value = saved;
  } catch { /* An unreadable local receipt never causes automatic submission. */ }
  void refresh(); timer = setInterval(refresh, 5000);
});
onBeforeUnmount(() => clearInterval(timer));
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
        placeholder="例如：打开小红书，搜索二糖，进入第一个相关内容作者的会话，静默5分钟后截图归档。只查看，不发送消息。" />
      <p class="text-sm text-muted-foreground">直接描述目标和选择条件。手机使用已配置的 AI 与业务资料执行，关键截图自动回传。</p>
      <button type="submit" :disabled="disabled" class="rounded-md bg-primary px-5 py-2 text-primary-foreground disabled:opacity-50">{{ busy ? '处理中…' : '交给这台手机' }}</button>
      <span v-if="active || pending" class="ml-3 text-sm text-muted-foreground">本台手机有任务正在执行或核对中</span>
    </form>
    <p v-if="error" role="alert" class="rounded border border-amber-500 p-3 text-sm">{{ error }}</p>
    <section aria-label="任务记录" class="space-y-4">
      <div class="flex items-center justify-between"><h2 class="text-lg font-semibold">任务记录</h2>
        <button :disabled="loading" class="rounded border px-3 py-1 text-sm" @click="refresh">刷新状态</button></div>
      <p v-if="loaded && !receipts.length" class="rounded border p-6 text-sm text-muted-foreground">还没有任务。输入本次需求即可开始。</p>
      <article v-for="task in receipts" :key="task.task_id" class="space-y-3 rounded-lg border bg-card p-4">
        <div class="flex flex-wrap justify-between gap-2"><strong>{{ names[task.status] || task.status }}</strong>
          <time class="text-sm text-muted-foreground">{{ new Date(task.created_at * 1000).toLocaleString() }}</time></div>
        <p class="whitespace-pre-wrap break-words">{{ task.goal }}</p>
        <p v-if="task.result?.answer || task.result?.message || task.result?.prompt" class="whitespace-pre-wrap break-words border-l-2 pl-3">{{ task.result?.answer || task.result?.message || task.result?.prompt }}</p>
        <p v-if="task.error" class="text-sm text-amber-700">{{ task.error }}</p>
        <p v-if="task.result?.note" class="text-sm text-muted-foreground">{{ task.result.note }}</p>
        <p v-if="task.status === 'done'" class="text-sm text-muted-foreground">执行已结束；实际结果请结合归档截图和复查记录核验。</p>
        <p v-if="task.status === 'waiting_for_answer' || task.status === 'waiting_on_phone'" class="text-sm text-amber-700">本轮需要补充处理，请查看手机上的提示。网页追问回复暂未接入。</p>
        <div class="flex gap-4 text-sm"><a :href="toHashHref('/evidence/' + domain + '?task=' + task.task_id)" class="underline">查看本次证据</a>
          <button v-if="!terminalTask(task.status)" :disabled="busy" class="underline" @click="cancel(task)">停止任务</button></div>
      </article>
    </section>
  </main>
</template>
