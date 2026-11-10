import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue';

export function deviceSocket(id: string, path: string) {
  const url = new URL(`/d/${encodeURIComponent(id)}${path}`, location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

export function useDeviceChannel(id: Ref<string>, online: Ref<boolean>) {
  const status = ref<Record<string, any>>({});
  const info = ref<Record<string, any>>({});
  const displays = ref<any[]>([]);
  const connected = ref(false);
  const sampled = ref(0);
  const clock = ref(Date.now());
  const visible = ref(!document.hidden);
  const logs = ref<{ time: string; message: string }[]>([]);
  let socket: WebSocket | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let poll: ReturnType<typeof setInterval> | undefined;
  let attempts = 0;
  let handshake: ReturnType<typeof setTimeout> | undefined;
  const pending = new Map<string, { resolve: (data: any) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  const enabled = computed(() => online.value && visible.value);
  const stale = computed(() => !connected.value || clock.value - sampled.value > 10000);
  function log(message: string) { logs.value.unshift({ time: new Date().toLocaleTimeString(), message }); logs.value.length = Math.min(logs.value.length, 80); }
  function command(command: string, data: unknown = []) {
    if (socket?.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify({ command, data, correlation_id: `bk-${Date.now()}-${Math.random().toString(36).slice(2, 7)}` }));
    return true;
  }
  function requestCommand(command: string, data: unknown = []): Promise<any> {
    if (socket?.readyState !== WebSocket.OPEN || !connected.value) return Promise.reject(new Error('手机控制通道未连接'));
    const ws = socket;
    const correlation_id = `bk-input-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(correlation_id); reject(new Error('手机输入响应超时')); }, 4000);
      pending.set(correlation_id, { resolve, reject, timer });
      try { ws.send(JSON.stringify({ command, data, correlation_id })); }
      catch { clearTimeout(timer); pending.delete(correlation_id); reject(new Error('手机输入发送失败')); }
    });
  }
  function request() {
    if (socket?.readyState !== WebSocket.OPEN) return;
    socket.send('status');
    command('device/info', { display: 0 });
    command('display/list', []);
  }
  function stop() {
    clearTimeout(retry); clearTimeout(handshake); clearInterval(poll);
    for (const p of pending.values()) { clearTimeout(p.timer); p.reject(new Error('手机控制通道已断开')); } pending.clear();
    if (socket) { socket.onopen = socket.onclose = socket.onerror = socket.onmessage = null; socket.close(); socket = undefined; }
    connected.value = false;
  }
  function connect() {
    stop();
    if (!enabled.value) return;
    log('正在连接手机状态通道');
    try {
      const ws = new WebSocket(deviceSocket(id.value, '/ws/command'));
      socket = ws;
      let lastReceived = Date.now();
      const recover = () => { if (socket !== ws) return; stop(); log('状态通道中断，等待重连'); if (enabled.value) retry = setTimeout(connect, attempts++ < 15 ? 1000 : 5000); };
      // Bound CONNECTING too: an unanswered upgrade used to hang indefinitely.
      handshake = setTimeout(recover, 5000);
      ws.onopen = () => { request(); poll = setInterval(() => { clock.value = Date.now(); if (Date.now() - lastReceived > 8000) recover(); else request(); }, 2000); };
      ws.onmessage = e => {
        try {
          const m = JSON.parse(e.data);
          lastReceived = Date.now();
          clearTimeout(handshake);
          if (!connected.value) { connected.value = true; attempts = 0; log('状态通道已连接'); }
          const waiting = pending.get(m.correlation_id);
          if (waiting) {
            clearTimeout(waiting.timer); pending.delete(m.correlation_id);
            if (m.error || /error/i.test(m.type) || m.data === false || m.data?.result === false) waiting.reject(new Error('手机拒绝输入指令'));
            else waiting.resolve(m.data);
          }
          if (m.type === 'status' && m.data && typeof m.data === 'object') { status.value = m.data; sampled.value = Date.now(); clock.value = Date.now(); }
          if (m.type === 'device/info' && m.data) info.value = m.data;
          if (m.type === 'display/list' && Array.isArray(m.data)) displays.value = m.data;
        } catch { /* Ignore non-JSON control acknowledgements. */ }
      };
      ws.onclose = recover; ws.onerror = recover;
    } catch { retry = setTimeout(connect, 5000); }
  }
  function visibility() { visible.value = !document.hidden; }
  watch(enabled, connect);
  onMounted(() => { document.addEventListener('visibilitychange', visibility); connect(); });
  onBeforeUnmount(() => { stop(); document.removeEventListener('visibilitychange', visibility); });
  return { status, info, displays, connected, stale, sampled, logs, command, requestCommand, log };
}
