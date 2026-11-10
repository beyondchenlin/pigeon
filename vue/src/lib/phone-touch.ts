/** One active, ordered input route. A failed gesture is never replayed. */
export type TouchPayload = { action: 'down' | 'move' | 'up'; id: number; x?: number; y?: number };
type Route = 'p2p' | 'wss' | 'none';
type Options = {
  url: string;
  ready: (ready: boolean, dimensions?: { width: number; height: number }) => void;
  lost: () => void;
  log: (message: string) => void;
  stats?: (route: Route, rtt: number | null) => void;
};

export function createPhoneTouch(o: Options) {
  let disposed = false, ws: WebSocket | undefined, dc: RTCDataChannel | undefined;
  let route: Route = 'none', gesture: Route = 'none', capability = false, claiming = false;
  let sequence = 0, lastRx = 0, retryCount = 0, notBefore = 0;
  let retry: ReturnType<typeof setTimeout> | undefined, handshake: ReturnType<typeof setTimeout> | undefined;
  let heartbeat: ReturnType<typeof setInterval> | undefined, claimTimer: ReturnType<typeof setTimeout> | undefined;
  const pending = new Map<number, number>();
  const dimensions = (m: any) => Number.isInteger(m.w) && m.w > 0 && m.w <= 16384 && Number.isInteger(m.h) && m.h > 0 && m.h <= 16384;
  function state(next: Route, m?: any) {
    route = next; o.ready(next !== 'none', m ? { width: m.w, height: m.h } : undefined); o.stats?.(next, null);
  }
  function stopWs() {
    clearTimeout(handshake);
    if (ws) { ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null; ws.close(); ws = undefined; }
  }
  function loseGesture() { gesture = 'none'; o.lost(); }
  function stopDc() {
    clearInterval(heartbeat); clearTimeout(claimTimer); pending.clear(); capability = claiming = false;
    if (dc) { dc.onopen = dc.onmessage = dc.onclose = dc.onerror = null; dc.close(); dc = undefined; }
  }
  function failDc() {
    if (!dc || disposed) return;
    const owned = route === 'p2p' || claiming;
    stopDc();
    if (owned) {
      state('none'); loseGesture(); o.log('点对点触控中断，正在恢复兼容通道');
      // Phone lease is 2.5 s. Do not let an old held contact overlap fallback.
      notBefore = performance.now() + 2700;
      clearTimeout(retry); retry = setTimeout(connectWs, 2700);
    }
  }
  function claim() {
    if (disposed || !capability || claiming || gesture !== 'none' || dc?.readyState !== 'open') return;
    if (performance.now() < notBefore) {
      clearTimeout(claimTimer); claimTimer = setTimeout(claim, notBefore - performance.now()); return;
    }
    clearTimeout(retry); stopWs(); state('none'); claiming = true;
    try { dc.send(JSON.stringify({ type: 'claim' })); }
    catch { failDc(); return; }
    claimTimer = setTimeout(failDc, 4000);
  }
  function connectWs() {
    if (disposed || route === 'p2p' || claiming) return;
    if (performance.now() < notBefore) {
      clearTimeout(retry); retry = setTimeout(connectWs, notBefore - performance.now()); return;
    }
    stopWs();
    const socket = new WebSocket(o.url); ws = socket;
    const failed = () => {
      if (ws !== socket || disposed) return;
      stopWs(); state('none'); loseGesture();
      clearTimeout(retry); retry = setTimeout(connectWs, retryCount++ < 10 ? 1000 : 5000);
    };
    socket.onopen = () => socket.send(JSON.stringify({ action: 'reset' }));
    socket.onmessage = e => {
      if (ws !== socket || disposed || claiming) return;
      try {
        const m = JSON.parse(e.data);
        if (m.type === 'OK' && dimensions(m)) { clearTimeout(handshake); retryCount = 0; state('wss', m); }
      } catch { /* Non-JSON native notices do not grant readiness. */ }
    };
    socket.onerror = socket.onclose = failed;
    handshake = setTimeout(failed, 5000);
  }
  function attach(channel: RTCDataChannel) {
    if (disposed || channel.label !== 'bk-touch-v1' || channel.protocol !== 'bk.touch.v1' || !channel.ordered) { channel.close(); return; }
    if (dc) failDc();
    dc = channel; sequence = 0; lastRx = performance.now();
    channel.onmessage = e => {
      if (dc !== channel || disposed || typeof e.data !== 'string' || e.data.length > 4096) return;
      try {
        const m = JSON.parse(e.data); lastRx = performance.now();
        if (m.type === 'capability' && m.version === 1) { capability = true; claim(); }
        else if (m.type === 'ready' && claiming && dimensions(m)) {
          clearTimeout(claimTimer); claiming = false; state('p2p', m); o.log('已切换点对点触控');
        } else if (m.type === 'ack' && Number.isInteger(m.seq) && m.seq <= sequence) {
          for (const seq of pending.keys()) if (seq <= m.seq) pending.delete(seq);
        } else if (m.type === 'pong' && typeof m.id === 'number' && m.id <= performance.now()) {
          if (route === 'p2p') o.stats?.('p2p', Math.round(performance.now() - m.id));
        }
      } catch { failDc(); }
    };
    channel.onclose = channel.onerror = () => { if (dc === channel) failDc(); };
    const opened = () => { lastRx = performance.now(); heartbeat = setInterval(() => {
      if (dc !== channel || disposed) return;
      const now = performance.now(), oldest = pending.values().next().value;
      // An idle/background page may throttle timers. The short lease protects
      // a held contact, not an otherwise healthy idle DTLS connection.
      if (channel.readyState === 'closed' || (now - lastRx > 2500 && (gesture !== 'none' || route !== 'p2p')) || (oldest !== undefined && now - oldest > 1000)) { failDc(); return; }
      if (channel.readyState === 'open') {
        try { channel.send(JSON.stringify({ type: 'ping', id: now })); } catch { failDc(); }
      }
    }, 500); };
    channel.onopen = opened;
    if (channel.readyState === 'open') opened();
  }
  function send(payload: TouchPayload) {
    if (disposed || route === 'none') return false;
    if (payload.action === 'down') { if (gesture !== 'none') return false; }
    else if (gesture !== route) return false;
    const active = route;
    try {
      if (active === 'p2p') {
        if (dc?.readyState !== 'open' || dc.bufferedAmount > 8192 || pending.size >= 20) { failDc(); return false; }
        const seq = ++sequence;
        dc.send(JSON.stringify({ type: 'touch', seq, ...payload })); pending.set(seq, performance.now());
      } else {
        if (ws?.readyState !== WebSocket.OPEN || ws.bufferedAmount > 8192) {
          stopWs(); state('none'); loseGesture(); retry = setTimeout(connectWs, 1000); return false;
        }
        ws.send(JSON.stringify(payload));
      }
    } catch {
      if (active === 'p2p') failDc();
      else { stopWs(); state('none'); loseGesture(); retry = setTimeout(connectWs, 1000); }
      return false;
    }
    gesture = payload.action === 'up' ? 'none' : active;
    if (gesture === 'none' && capability && route !== 'p2p') claim();
    return true;
  }
  connectWs();
  return {
    attach, send,
    get bufferedAmount() { return route === 'p2p' ? dc?.bufferedAmount ?? 0 : ws?.bufferedAmount ?? 0; },
    recover() { if (route === 'none' && !claiming && !dc) { clearTimeout(retry); connectWs(); } },
    close() {
      if (disposed) return;
      if (gesture !== 'none') send({ action: 'up', id: 0 });
      disposed = true; clearTimeout(retry); stopDc(); stopWs(); state('none'); loseGesture();
    }
  };
}
