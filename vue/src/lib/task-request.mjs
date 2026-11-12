// Same UUID v5 mapping as the existing MCP/API adapter. Store before any POST.
export async function prepareTask(domain, goal, requestId = crypto.randomUUID()) {
  if (!/^[a-z0-9]{1,64}$/.test(domain) || !goal.trim() || goal.trim().length > 4000)
    throw new Error('请输入本次要完成的任务（最多4000字）。');
  const namespace = Uint8Array.from('6ba7b8119dad11d180b400c04fd430c8'.match(/../g), x => parseInt(x, 16));
  const name = new TextEncoder().encode(`pigeon-bk:${domain}:${requestId}`);
  const bytes = new Uint8Array(namespace.length + name.length);
  bytes.set(namespace); bytes.set(name, namespace.length);
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-1', bytes)).slice(0, 16);
  digest[6] = (digest[6] & 15) | 80; digest[8] = (digest[8] & 63) | 128;
  const hex = Array.from(digest, x => x.toString(16).padStart(2, '0')).join('');
  const taskId = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  return { taskId, requestId, body: { goal: goal.trim(), request_id: requestId } };
}

export const terminalTask = status => ['done', 'failed', 'cancelled', 'rejected'].includes(status);

// At most one creation attempt. On any uncertain response the caller only reads taskId.
export async function dispatchTask(request, persist, post) {
  await persist({ taskId: request.taskId, requestId: request.requestId });
  return post(request.body);
}
