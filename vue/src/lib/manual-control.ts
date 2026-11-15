import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue';
import { apiRequest } from './api';

type Reply = { state: string; task_id?: string };
const headers = { 'X-Requested-With': 'BK-Task' };
const IDLE_MS = 5000;

export function useManualControl(deviceId: Ref<string>, online: Ref<boolean>, inputBusy: () => boolean,
                                 closeInput: () => void, notify: (message: string) => void) {
  const held = ref(false);
  let session: { token: string; url: string } | undefined;
  let claiming: Promise<boolean> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let validUntil = 0;
  let renewing = false;

  function activity() {
    clearTimeout(timer);
    if (held.value) timer = setTimeout(() => inputBusy() ? activity() : release(), IDLE_MS);
  }
  function release() {
    const old = session;
    session = undefined;
    claiming = undefined;
    clearTimeout(timer);
    clearInterval(heartbeat);
    renewing = false;
    if (held.value) closeInput();
    held.value = false;
    if (old) void apiRequest(old.url, { method: 'POST', headers, keepalive: true,
      body: JSON.stringify({ action: 'release', token: old.token }) }).catch(() => {});
  }
  async function acquire() {
    if (!online.value || document.hidden) return false;
    if (held.value && Date.now() < validUntil) { activity(); return true; }
    if (held.value) release();
    if (claiming) return claiming;
    const own = { url: '/api/v1/device/' + encodeURIComponent(deviceId.value) + '/ai/manual-control', token: crypto.randomUUID() };
    session = own;
    const request = async (action: string) => {
      const sent = Date.now();
      const reply = await apiRequest<Reply>(own.url, { method: 'POST', headers,
        body: JSON.stringify({ action, token: own.token }), signal: AbortSignal.timeout(4000) });
      if (session === own && reply.data?.state === 'held') validUntil = sent + 10000;
      return reply.data;
    };
    claiming = (async () => {
      try {
        let reply = await request('claim');
        if (session !== own) return false;
        let stoppedTask = false;
        if (reply?.state === 'ai' && reply.task_id) {
          const task = reply.task_id;
          if (!window.confirm('AI 正在操作这台手机。停止这次任务并接管手机？') || session !== own) return false;
          notify('正在等待 AI 停止…');
          await apiRequest('/api/v1/device/' + encodeURIComponent(deviceId.value) + '/ai/tasks/' + task + '/takeover',
            { method: 'POST', headers, body: '{}', signal: AbortSignal.timeout(12000) });
          const until = Date.now() + 12000;
          while (session === own) {
            reply = await request('claim');
            if (reply?.state !== 'ai' || reply.task_id !== task || Date.now() >= until) break;
            await new Promise(resolve => setTimeout(resolve, 650));
          }
          if (session !== own) return false;
          if (reply?.state === 'ai') { notify('AI 仍在执行，本次操作未发送。'); return false; }
          stoppedTask = true;
        }
        if (reply?.state !== 'held') {
          notify(reply?.state === 'manual' ? '其他会话正在接管手机，本次操作未发送。' : '手机当前不可接管，本次操作未发送。');
          return false;
        }
        held.value = true;
        if (stoppedTask) notify('AI 已停止，已接管。请在当前画面上操作。');
        activity();
        heartbeat = setInterval(async () => {
          if (session !== own || renewing) return;
          renewing = true;
          try {
            const renewed = await request('renew');
            if (session === own && renewed?.state !== 'held') release();
          } catch { if (session === own) release(); }
          finally { if (session === own) renewing = false; }
        }, 3500);
        // The screen may have changed during cancellation. Discard that original input.
        return !stoppedTask;
      } catch { if (session === own) notify('未能确认接管，本次操作未发送。'); return false; }
      finally { if (session === own) { claiming = undefined; if (!held.value) release(); } }
    })();
    return claiming;
  }
  const visibility = () => { if (document.hidden) release(); };
  watch([deviceId, online], release, { flush: 'sync' });
  onMounted(() => { document.addEventListener('visibilitychange', visibility); window.addEventListener('pagehide', release); });
  onBeforeUnmount(() => {
    document.removeEventListener('visibilitychange', visibility);
    window.removeEventListener('pagehide', release);
    release();
  });
  return { held, acquire, activity };
}
