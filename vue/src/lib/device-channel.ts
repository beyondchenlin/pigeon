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
  const enabled = computed(() => online.value && visible.value);
  const stale = computed(() => !connected.value || clock.value - sampled.value > 10000);
  function log(message: string) { logs.value.unshift({ time: new Date().toLocaleTimeString(), message }); logs.value.length = Math.min(logs.value.length, 80); }
  function command(command: string, data: unknown = []) {
    if (socket?.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify({ command, data, correlation_id: `bk-${Date.now()}-${Math.random().toString(36).slice(2, 7)}` }));
    return true;
  }
  function request() {
    if (socket?.readyState !== WebSocket.OPEN) return;
    socket.send('status');
    command('device/info', { display: 0 });
    command('display/list', []);
  }
  function stop() {
    clearTimeout(retry); clearInterval(poll);
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
      const recover = () => { if (socket !== ws) return; stop(); log('状态通道中断，等待重连'); if (enabled.value) retry = setTimeout(connect, Math.min(30000, 2000 * 2 ** Math.min(attempts++, 4))); };
      ws.onopen = () => { connected.value = true; attempts = 0; log('状态通道已连接'); request(); poll = setInterval(() => { clock.value = Date.now(); if (Date.now() - lastReceived > 15000) recover(); else request(); }, 3000); };
      ws.onmessage = e => {
        try {
          const m = JSON.parse(e.data);
          lastReceived = Date.now();
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
  return { status, info, displays, connected, stale, sampled, logs, command, log };
}
