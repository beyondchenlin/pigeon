import test from 'node:test';
import assert from 'node:assert/strict';
import { startRtcVideo, startPhoneVideo } from '../src/lib/phone-video.ts';
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup(t, combined = false, extra = {}) {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  t.mock.timers.setTime(100000);
  const sockets = [], peers = [], videos = [], failures = [], frames = [], modes = [], snapshots = [], details = [];
  let ready = 0;
  class Socket {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; this.closed = 0; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    open() { this.readyState = 1; this.onopen?.(); }
    receive(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
    close() { this.closed++; this.readyState = 3; }
  }
  class Peer {
    constructor() { this.calls = []; this.connectionState = this.iceConnectionState = 'new'; this.stopped = 0; peers.push(this); }
    async createOffer() { return { type: 'offer', sdp: 'test' }; }
    createDataChannel(label, options) { this.channel = { label, ...options }; this.calls.push('datachannel'); return this.channel; }
    async setLocalDescription() {}
    async setRemoteDescription() { this.calls.push('answer'); }
    async addIceCandidate(c) { this.calls.push(c ? 'candidate' : 'complete'); }
    getReceivers() { return [{ track: { stop: () => this.stopped++ } }]; }
    close() { this.closed = true; }
    async getStats() { return new Map(); }
  }
  const doc = { createElement(tag) {
    if(tag==='canvas')return {getContext:()=>({drawImage(){},clearRect(){},getImageData:()=>({data:new Uint8ClampedArray(24*48*4)})})};
    const video = { readyState: 0, currentTime: 0, decoded: 0, videoWidth: 0, videoHeight: 0, async play() {}, pause() {},
      getVideoPlaybackQuality() { return { totalVideoFrames: this.decoded }; },
      requestVideoFrameCallback(cb) { this.callback = cb; return 1; }, cancelVideoFrameCallback() { this.callback = null; },
      emit() { this.readyState = 4; this.currentTime++; this.decoded++; this.videoWidth = 540; this.videoHeight = 1194; this.callback?.(); }
    }; videos.push(video); return video;
  }};
  const restores = [];
  for (const [name, value] of Object.entries({ WebSocket: Socket, RTCPeerConnection: Peer, document: doc, performance: { now: () => Date.now() }, cancelAnimationFrame() {}, createImageBitmap: extra.bitmap ?? (async()=>{}) })) {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
    restores.push(() => descriptor ? Object.defineProperty(globalThis, name, descriptor) : delete globalThis[name]);
  }
  if (extra.mounted) { delete extra.mounted; extra.video = doc.createElement('video'); }
  const stop = combined ? startPhoneVideo({ url: p => p, decoderUrl: 'https://example.test/decoder.js', canvas: { getContext: () => ({ drawImage() {},clearRect(){} }) }, snapshot:(short,signal)=>new Promise(resolve=>snapshots.push({short,signal,resolve})),detail:value=>details.push(value), frame: (w,h) => frames.push([w,h]), mode: m => modes.push(m), state() {}, log() {}, ...extra }) : startRtcVideo({ url: p => p, ready: () => ready++, frame: v => frames.push(v.currentTime), failed: why => failures.push(why), ...extra });
  t.after(() => { stop(); restores.forEach(restore => restore()); });
  return { sockets, peers, videos, failures, frames, modes, snapshots, details, stop, get ready() { return ready; } };
}

test('keeps fallback until a real drawable RTC frame arrives', async t => {
  const s = setup(t); s.sockets[0].open(); await flush();
  assert.equal(s.ready, 0);
  assert.equal(s.sockets[0].sent[0].type, 'request-offer');
  s.videos[0].emit(); assert.equal(s.ready, 1); assert.equal(s.frames.length, 1);
  s.videos[0].emit(); assert.equal(s.ready, 1);
  t.mock.timers.tick(13000); assert.deepEqual(s.failures, []);
});

test('touch data channel is negotiated on the video peer before its offer', async t => {
  const channels = [], s = setup(t, true, { inputChannel: c => channels.push(c) });
  assert.equal(channels.length, 1);
  assert.equal(channels[0], s.peers[0].channel);
  assert.deepEqual(channels[0], { label: 'bk-touch-v1', ordered: true, protocol: 'bk.touch.v1' });
  s.sockets[0].open(); await flush();
  assert.equal(s.sockets[0].sent[0].type, 'request-offer');
  s.stop(); assert.equal(s.peers[0].closed, true);
});

test('mounted RTC presents without canvas copies, retains dimensions and releases the stream', t => {
  let copies = 0;
  const canvas = { getContext: () => ({ drawImage() { copies++; } }) };
  const s = setup(t, true, { mounted: true, canvas }), v = s.videos[0];
  assert.equal(s.videos.length, 1); // Reuse the provided DOM element.
  v.emit(); assert.equal(s.frames.length, 0); // Never promote a stale frame.
  const receiver = { jitterBufferTarget: null }, stream = {};
  s.peers[0].ontrack({ track: { kind: 'video' }, streams: [stream], receiver });
  assert.equal(receiver.jitterBufferTarget, 20);
  v.emit(); assert.deepEqual(s.frames, [[540, 1194]]);
  assert.equal(copies, 0); assert.equal(canvas.width, 540); assert.equal(canvas.height, 1194);
  assert.equal(v.srcObject, stream);
  s.stop(); assert.equal(v.srcObject, null); assert.equal(v.callback, null);
  assert.equal(s.peers[0].stopped, 1);
});

test('buffer preference cannot break receivers that reject or lack the optional setter', t => {
  const s = setup(t);
  const receiver = { set jitterBufferTarget(value) { throw new Error('unsupported'); } };
  for (const r of [receiver, {}]) {
    s.peers[0].ontrack({ track: { kind: 'video' }, streams: [{}], receiver: r });
  }
  s.videos[0].emit(); assert.equal(s.ready, 1); assert.deepEqual(s.failures, []);
});

test('mounted surface survives RTC failure, JPEG fallback, and a fresh RTC track', t => {
  const s = setup(t, true, { mounted: true }), v = s.videos[0];
  s.peers[0].ontrack({ track: { kind: 'video' }, streams: [{}], receiver: {} }); v.emit();
  s.peers[0].connectionState = 'failed'; s.peers[0].onconnectionstatechange();
  assert.equal(v.srcObject, null); assert.equal(s.modes.at(-1), 'mjpeg');
  const fallback = s.snapshots[0];
  t.mock.timers.tick(1000); v.emit(); assert.equal(fallback.signal.aborted, false);
  s.peers[1].ontrack({ track: { kind: 'video' }, streams: [{}], receiver: {} }); v.emit();
  assert.equal(s.videos.length, 1); assert.equal(fallback.signal.aborted, true);
  assert.equal(s.modes.at(-1), 'webrtc');
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

test('RTC starts alone, failed media opens a JPEG fallback, recovery closes it', t => {
  const s = setup(t, true);
  assert.equal(s.sockets.length, 1); assert.deepEqual(s.modes, ['webrtc']);
  s.videos[0].emit(); assert.equal(s.modes.at(-1), 'webrtc');
  s.peers[0].connectionState = 'failed'; s.peers[0].onconnectionstatechange();
  const fallback = s.snapshots[0];
  assert.equal(fallback.short,360);assert.ok(s.sockets.every(x=>!x.url.includes('/live')));
  assert.equal(s.modes.at(-1), 'mjpeg'); assert.equal(fallback.signal.aborted, false);
  t.mock.timers.tick(1000); s.videos[1].emit();
  assert.equal(fallback.signal.aborted, true); assert.equal(s.modes.at(-1), 'webrtc');
  const count = s.sockets.length; s.stop(); t.mock.timers.tick(60000);
  assert.equal(s.sockets.length, count); assert.ok(s.sockets.every(x => x.closed === 1));
});

test('failed RTC negotiation leaves one existing screenshot request alive', t => {
  const s = setup(t, true);
  s.sockets.find(x => x.url.includes('/signaling')).onerror();
  const fallback = s.snapshots[0];
  t.mock.timers.tick(2000); s.sockets.at(-1).onerror();
  assert.equal(fallback.signal.aborted, false); assert.equal(s.snapshots.length, 1);
  s.stop(); assert.equal(fallback.signal.aborted, true);
});

test('confirmed command recovery bypasses video backoff without duplicate attempts', t => {
  const s = setup(t, true); s.sockets[0].onerror();
  const fallback=s.snapshots[0];
  s.stop.recover(); assert.equal(s.peers.length,2); assert.equal(fallback.signal.aborted,false);
  s.stop.recover(); assert.equal(s.peers.length,2);
  s.videos[1].emit(); t.mock.timers.tick(6000);
  assert.equal(s.peers.length,2);assert.equal(fallback.signal.aborted,true);
});

test('late ICE credentials cannot create a peer after the page stops', async t => {
  let resolve;
  const s = setup(t, true, { loadIceServers: () => new Promise(r => resolve = r) });
  assert.equal(s.peers.length, 0); assert.equal(s.sockets.length, 0);
  s.stop(); resolve([]); await flush();
  assert.equal(s.peers.length, 0); assert.equal(s.sockets.length, 0);
});

test('credential endpoint failure attempts direct RTC before opening fallback', async t => {
  const s = setup(t, true, { loadIceServers: async () => { throw new Error('timeout'); } });
  await flush(); assert.equal(s.peers.length, 1);
  assert.equal(s.sockets.filter(x => x.url.includes('/live')).length, 0);
});

test('latest decoded frame is painted when compositor callbacks stall, without duplicate painting', t => {
  const s = setup(t), v = s.videos[0];
  v.emit(); assert.equal(s.frames.length, 1);
  v.currentTime += 1; v.decoded += 3; // Frames decoded, but no video callback.
  t.mock.timers.tick(200); assert.equal(s.frames.length, 2);
  t.mock.timers.tick(1000); assert.equal(s.frames.length, 2);
  v.callback(); assert.equal(s.frames.length, 2);
  s.stop(); v.decoded++; t.mock.timers.tick(1000); assert.equal(s.frames.length, 2);
});

test('explicit compatibility mode starts JPEG without a peer', t => {
  const s = setup(t, true, { webrtc: false });
  assert.equal(s.peers.length, 0); assert.equal(s.sockets.length, 0);
  assert.equal(s.snapshots.length,1);
});

test('fallback serializes reads and closes an in-flight bitmap after disposal', async t => {
  const pending=[];
  const s = setup(t, true, { webrtc: false,bitmap:blob => new Promise(resolve => pending.push({ blob, resolve })) });
  s.snapshots[0].resolve(new Blob(['first'])); await flush();
  assert.equal(pending.length, 1);
  t.mock.timers.tick(5000);assert.equal(s.snapshots.length,1);
  let closed = 0;
  pending[0].resolve({ width: 540, height: 1194, close() { closed++; } }); await flush();
  t.mock.timers.tick(1500);assert.equal(s.snapshots.length,2);
  s.snapshots[1].resolve(new Blob(['second']));await flush();
  s.stop(); pending[1].resolve({ width: 540, height: 1194, close() { closed++; } }); await flush();
  assert.equal(closed, 2); assert.equal(s.frames.length, 1);
});

test('late idle detail is discarded after interaction, successful detail clears on next input',async t=>{
  const s=setup(t,true,{mounted:true}),v=s.videos[0];
  let closed=0;t.mock.method(globalThis,'createImageBitmap',async()=>({width:720,height:1592,close(){closed++;}}));
  s.peers[0].ontrack({track:{kind:'video'},streams:[{}],receiver:{}});v.emit();
  t.mock.timers.tick(11000);assert.equal(s.snapshots[0].short,720);
  s.stop.interact();assert.equal(s.snapshots[0].signal.aborted,true);
  s.snapshots[0].resolve(new Blob(['late']));await flush();assert.equal(s.details.includes(true),false);
  t.mock.timers.tick(11000);s.snapshots[1].resolve(new Blob(['fresh']));await flush();
  assert.equal(s.details.at(-1),true);s.stop.interact();assert.equal(s.details.at(-1),false);assert.equal(closed,2);
});
