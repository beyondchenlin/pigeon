import test from 'node:test';
import assert from 'node:assert/strict';
import { startRtcVideo } from '../src/lib/phone-video.ts';
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup(t) {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const sockets = [], peers = [], videos = [], failures = [], frames = [];
  let ready = 0;
  class Socket {
    static OPEN = 1;
    constructor() { this.readyState = 0; this.sent = []; this.closed = 0; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    open() { this.readyState = 1; this.onopen?.(); }
    receive(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
    close() { this.closed++; this.readyState = 3; }
  }
  class Peer {
    constructor() { this.calls = []; this.connectionState = this.iceConnectionState = 'new'; this.stopped = 0; peers.push(this); }
    async createOffer() { return { type: 'offer', sdp: 'test' }; }
    async setLocalDescription() {}
    async setRemoteDescription() { this.calls.push('answer'); }
    async addIceCandidate(c) { this.calls.push(c ? 'candidate' : 'complete'); }
    getReceivers() { return [{ track: { stop: () => this.stopped++ } }]; }
    close() { this.closed = true; }
    async getStats() { return new Map(); }
  }
  const doc = { createElement() {
    const video = { readyState: 0, currentTime: 0, videoWidth: 0, videoHeight: 0, async play() {}, pause() {},
      requestVideoFrameCallback(cb) { this.callback = cb; return 1; }, cancelVideoFrameCallback() { this.callback = null; },
      emit() { this.readyState = 4; this.currentTime++; this.videoWidth = 540; this.videoHeight = 1194; this.callback?.(); }
    }; videos.push(video); return video;
  }};
  const restores = [];
  for (const [name, value] of Object.entries({ WebSocket: Socket, RTCPeerConnection: Peer, document: doc, cancelAnimationFrame() {} })) {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
    restores.push(() => descriptor ? Object.defineProperty(globalThis, name, descriptor) : delete globalThis[name]);
  }
  const stop = startRtcVideo({ url: p => p, ready: () => ready++, frame: v => frames.push(v.currentTime), failed: why => failures.push(why) });
  t.after(() => { stop(); restores.forEach(restore => restore()); });
  return { sockets, peers, videos, failures, frames, stop, get ready() { return ready; } };
}

test('keeps fallback until a real drawable RTC frame arrives', async t => {
  const s = setup(t); s.sockets[0].open(); await flush();
  assert.equal(s.ready, 0);
  assert.equal(s.sockets[0].sent[0].type, 'request-offer');
  s.videos[0].emit(); assert.equal(s.ready, 1); assert.equal(s.frames.length, 1);
  s.videos[0].emit(); assert.equal(s.ready, 1);
  t.mock.timers.tick(13000); assert.deepEqual(s.failures, []);
});

test('remote ICE arriving before the answer is applied in order', async t => {
  const s = setup(t), ws = s.sockets[0]; ws.open(); await flush();
  ws.receive({ type: 'candidate', data: { candidate: 'test' } });
  ws.receive({ type: 'ice-candidates-complete' });
  ws.receive({ type: 'answer', data: { type: 'answer', sdp: 'test' } });
  await flush(); assert.deepEqual(s.peers[0].calls, ['answer', 'candidate', 'complete']);
});

test('late async offer cannot send after disposal', async t => {
  const s = setup(t); let resolve;
  s.peers[0].createOffer = () => new Promise(r => resolve = r);
  s.sockets[0].open(); s.stop(); resolve({ type: 'offer', sdp: 'late' }); await flush();
  assert.deepEqual(s.sockets[0].sent, []); assert.equal(s.peers[0].stopped, 1);
  assert.equal(s.peers[0].closed, true); assert.deepEqual(s.failures, []);
});

test('first-frame deadline closes the failed attempt exactly once', t => {
  const s = setup(t); const late = s.videos[0].callback;
  t.mock.timers.tick(12000); late(); t.mock.timers.tick(60000);
  assert.equal(s.failures.length, 1); assert.equal(s.sockets[0].closed, 1);
  assert.equal(s.frames.length, 0); assert.equal(s.ready, 0);
});

test('signaling close does not interrupt active media, ICE failure does', t => {
  const s = setup(t); s.videos[0].emit(); s.sockets[0].onclose();
  assert.equal(s.failures.length, 0);
  s.peers[0].iceConnectionState = 'failed'; s.peers[0].oniceconnectionstatechange();
  assert.equal(s.failures.length, 1); assert.equal(s.peers[0].closed, true);
});

test('transient disconnect recovers before the grace deadline', t => {
  const s = setup(t); s.videos[0].emit();
  s.peers[0].connectionState = 'disconnected'; s.peers[0].onconnectionstatechange();
  t.mock.timers.tick(2000);
  s.peers[0].connectionState = 'connected'; s.peers[0].onconnectionstatechange();
  t.mock.timers.tick(2000); assert.deepEqual(s.failures, []);
  s.peers[0].connectionState = 'disconnected'; s.peers[0].onconnectionstatechange();
  t.mock.timers.tick(3000); assert.equal(s.failures.length, 1);
});
