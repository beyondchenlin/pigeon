
function integer(value, name, minimum, maximum) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum)
    throw new Error(`${name}须为${minimum}至${maximum}的整数。`);
  return value;
}

function exactFields(value, fields, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).length !== fields.length || fields.some(key => !Object.hasOwn(value, key)) ||
      Object.keys(value).some(key => !fields.includes(key)))
    throw new Error(`${name}包含缺失或不支持的参数。`);
}

export function validateExecution(value) {
  exactFields(value, ['version', 'kind', 'duration_s', 'clock', 'finish_timeout_s', 'stop_timeout_s',
    'progress_interval_s', 'event_latency_s', 'budget', 'progress_requirements'], '浏览执行约定');
  if (value.version !== 1 || value.kind !== 'browse' || value.clock !== 'elapsed')
    throw new Error('当前只支持第一版浏览任务，自手机实际开始按自然经过时间计时。');
  integer(value.duration_s, '目标时长（秒）', 1, 10000);
  integer(value.finish_timeout_s, '到期收尾（秒）', 1, 120);
  integer(value.stop_timeout_s, '停止确认（秒）', 1, 120);
  integer(value.progress_interval_s, '普通进度间隔（秒）', 1, 3600);
  integer(value.event_latency_s, '异常与结束通知目标（秒）', 1, 30);
  exactFields(value.budget, ['max_tokens'], '模型预算');
  integer(value.budget.max_tokens, '模型词元上限', 1, 10000000);
  exactFields(value.progress_requirements, ['min_observations', 'min_active_s', 'max_stall_s'], '有效进展要求');
  integer(value.progress_requirements.min_observations, '最低有效观察次数', 2, 1000000);
  integer(value.progress_requirements.min_active_s, '最低有效执行时长（秒）', 1, value.duration_s);
  integer(value.progress_requirements.max_stall_s, '最长停滞（秒）', 1, 10800);
  return { ...value, budget: {...value.budget}, progress_requirements: {...value.progress_requirements} };
}

export const supervisionSeconds = execution => 120 + execution.duration_s + execution.finish_timeout_s +
  execution.event_latency_s + execution.stop_timeout_s;

// Same UUID v5 mapping as the existing MCP/API adapter. Store before any POST.
export async function prepareTask(domain, goal, requestId = crypto.randomUUID(), options = {}) {
  if (!/^[a-z0-9]{1,64}$/.test(domain) || !goal.trim() || goal.trim().length > 4000)
    throw new Error('请输入本次要完成的任务（最多4000字）。');
  if (!options || typeof options !== 'object' || Array.isArray(options) ||
      Object.keys(options).some(key => !['timeout_s', 'execution'].includes(key)))
    throw new Error('任务包含不支持的执行参数。');
  const execution = Object.hasOwn(options, 'execution') ? validateExecution(options.execution) : undefined;
  const minimumTimeout = execution ? supervisionSeconds(execution) : 15;
  const timeout = options.timeout_s === undefined ? (execution ? minimumTimeout : 900) : options.timeout_s;
  if (!Number.isInteger(timeout) || timeout < 15 || timeout > 10800)
    throw new Error('任务超时上限须为15至10800秒的整数。');
  if (timeout < minimumTimeout) throw new Error(`服务器监督须至少覆盖启动、执行和收尾，共${minimumTimeout}秒。`);
  const namespace = Uint8Array.from('6ba7b8119dad11d180b400c04fd430c8'.match(/../g), x => parseInt(x, 16));
  const name = new TextEncoder().encode(`pigeon-bk:${domain}:${requestId}`);
  const bytes = new Uint8Array(namespace.length + name.length);
  bytes.set(namespace); bytes.set(name, namespace.length);
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-1', bytes)).slice(0, 16);
  digest[6] = (digest[6] & 15) | 80; digest[8] = (digest[8] & 63) | 128;
  const hex = Array.from(digest, x => x.toString(16).padStart(2, '0')).join('');
  const taskId = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  return { taskId, requestId, body: { goal: goal.trim(), request_id: requestId, timeout_s: timeout,
    ...(execution ? {execution} : {}) } };
}

export const terminalTask = status => ['done', 'failed', 'cancelled', 'rejected'].includes(status);

export const stoppingTask = task => !terminalTask(task.status) &&
  (task.cancel_requested === true || ['stopping', 'stop_unconfirmed'].includes(task.status));

// A delayed list/query response must never reopen a terminal task or erase a stop request.
export function mergeTaskReceipt(previous, incoming) {
  if (!previous || previous.task_id !== incoming.task_id) return incoming;
  if (terminalTask(previous.status) && previous.status !== incoming.status) return previous;
  if (Number.isFinite(previous.updated_at) && Number.isFinite(incoming.updated_at) &&
      incoming.updated_at < previous.updated_at) return previous;
  return { ...previous, ...incoming,
    cancel_requested: terminalTask(incoming.status) ? incoming.cancel_requested :
      previous.cancel_requested || incoming.cancel_requested };
}

export function mergeTaskReceipts(previous, incoming) {
  const old = new Map(previous.map(task => [task.task_id, task]));
  const ids = new Set(incoming.map(task => task.task_id));
  return [...incoming.map(task => mergeTaskReceipt(old.get(task.task_id), task)),
    ...previous.filter(task => !ids.has(task.task_id) && !terminalTask(task.status))];
}

// At most one creation attempt. On any uncertain response the caller only reads taskId.
export async function dispatchTask(request, persist, post) {
  await persist({ taskId: request.taskId, requestId: request.requestId });
  return post(request.body);
}

// A snapshot is never a live claim while its status or connection is uncertain.
export function taskProgress(task, connectionUncertain = false) {
  const text = value => {
    if (typeof value === 'string') return value.slice(0, 2000);
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    if (value && typeof value === 'object') return JSON.stringify(value).slice(0, 2000);
    return '';
  };
  return { progress: text(task.result?.progress), step: text(task.result?.step),
    stale: connectionUncertain || task.stale === true || task.status === 'unknown' };
}
