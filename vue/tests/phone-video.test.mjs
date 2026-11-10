import test from 'node:test';
import assert from 'node:assert/strict';
import { startRtcVideo, startPhoneVideo } from '../src/lib/phone-video.ts';
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup(t, combined = false, extra = {}) {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  t.mock.timers.setTime(100000);
  const sockets = [], peers = [], videos = [], failures = [], frames = [], modes = [], snapshots = [], details = [];
  let ready = 0;
  const probePixels=extra.probePixels??(()=>new Uint8ClampedArray(24*48*4));delete extra.probePixels;
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
    if(tag==='canvas')return {getContext:()=>({drawImage(){},clearRect(){},getImageData:()=>({data:probePixels()})})};
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

test('stalled input response opens fresh screenshots without closing P2P, then returns on new video',async t=>{
 const s=setup(t,true,{mounted:true}),v=s.videos[0],pc=s.peers[0];
 let decoded=1,keys=1;
 pc.getStats=async()=>new Map([['video',{id:'video',type:'inbound-rtp',kind:'video',framesDecoded:decoded,keyFramesDecoded:keys,packetsReceived:10}]]);
 pc.ontrack({track:{kind:'video'},streams:[{}],receiver:{}});v.emit();
 t.mock.timers.tick(1000);await flush();
 s.stop.interact();t.mock.timers.tick(2000);await flush();
 assert.equal(s.modes.at(-1),'mjpeg');assert.equal(s.snapshots[0].short,360);
 assert.equal(pc.closed,undefined);assert.equal(s.peers.length,1);
 decoded++;v.emit();assert.equal(s.modes.at(-1),'mjpeg'); // A P frame is insufficient.
 keys++;t.mock.timers.tick(1000);await flush();
 assert.equal(s.modes.at(-1),'mjpeg'); // Stats alone must not reveal a stale surface.
 decoded++;v.emit();
 assert.equal(s.modes.at(-1),'webrtc');assert.equal(s.snapshots[0].signal.aborted,true);
 assert.equal(s.peers.length,1);
});

test('a persistent media stall replaces only its peer after the bounded screenshot rescue',async t=>{
 const s=setup(t,true),v=s.videos[0],pc=s.peers[0];
 pc.getStats=async()=>new Map([['video',{id:'video',type:'inbound-rtp',kind:'video',framesDecoded:1,packetsReceived:10}]]);
 v.emit();t.mock.timers.tick(1000);await flush();s.stop.interact();
 t.mock.timers.tick(2000);await flush();assert.equal(s.modes.at(-1),'mjpeg');
 t.mock.timers.tick(7000);await flush();assert.equal(pc.closed,undefined);
 t.mock.timers.tick(1000);await flush();assert.equal(pc.closed,true);
 t.mock.timers.tick(1000);await flush();assert.equal(s.peers.length,2);
});

test('decoded delta frames cannot indefinitely delay keyframe recovery',async t=>{
 const s=setup(t,true),v=s.videos[0],pc=s.peers[0];let decoded=1;
 pc.getStats=async()=>new Map([['video',{id:'video',type:'inbound-rtp',kind:'video',framesDecoded:decoded,keyFramesDecoded:1,packetsReceived:decoded*10}]]);
 v.emit();t.mock.timers.tick(1000);await flush();s.stop.interact();
 t.mock.timers.tick(2000);await flush();assert.equal(s.modes.at(-1),'mjpeg');
 for(let i=0;i<7;i++){decoded++;v.emit();t.mock.timers.tick(1000);await flush();}
 assert.equal(pc.closed,undefined);assert.equal(s.modes.at(-1),'mjpeg');
 decoded++;v.emit();t.mock.timers.tick(1000);await flush();assert.equal(pc.closed,true);
});

test('static idle video keeps its peer and does not start screenshot rescue',async t=>{
 const s=setup(t,true),pc=s.peers[0];
 pc.getStats=async()=>new Map([['video',{id:'video',type:'inbound-rtp',kind:'video',framesDecoded:1,packetsReceived:10}]]);
 s.videos[0].emit();
 for(let i=0;i<30;i++){t.mock.timers.tick(1000);await flush();}
 assert.equal(pc.closed,undefined);assert.equal(s.snapshots.length,0);
});

test('policy changes do not reconnect an identical effective profile after encoder quarantine',async t=>{
 const profiles=[],s=setup(t,true,{profile:p=>profiles.push(p)});
 let count=0,rtt=.05;
 const stats=async()=>new Map([
  ['video',{id:'video',type:'inbound-rtp',kind:'video',framesDecoded:++count,packetsReceived:count*10}],
  ['transport',{id:'transport',type:'transport',selectedCandidatePairId:'pair'}],
  ['pair',{id:'pair',type:'candidate-pair',currentRoundTripTime:rtt}]
 ]);
 s.peers[0].connectionState='connected';t.mock.timers.tick(12000);await flush();
 t.mock.timers.tick(1000);await flush();assert.deepEqual(profiles.map(p=>p.short),[360,240]);
 s.peers[1].getStats=stats;s.videos[1].emit();rtt=.4;
 for(let i=0;i<8;i++){t.mock.timers.tick(1000);await flush();}
 assert.equal(s.peers.length,2);assert.equal(s.peers[1].closed,undefined);
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

test('a real presentation is processed even if the watchdog already saw its decode count',t=>{
 const s=setup(t),v=s.videos[0];v.emit();
 v.decoded++;t.mock.timers.tick(200); // New decode count, old compositor surface.
 assert.equal(s.frames.length,2);
 v.currentTime++;v.callback(100300,{presentedFrames:2});
 assert.equal(s.frames.length,3);assert.equal(s.frames.at(-1),v.currentTime);
 t.mock.timers.tick(1000);assert.equal(s.frames.length,3);
});

test('presented input feedback bypasses a recent stale watchdog sample and prevents false rescue',async t=>{
 let pixels=new Uint8ClampedArray(24*48*4),decoded=1;
 const s=setup(t,true,{mounted:true,probePixels:()=>pixels}),v=s.videos[0],pc=s.peers[0];
 pc.getStats=async()=>new Map([['video',{id:'video',type:'inbound-rtp',kind:'video',framesDecoded:decoded,keyFramesDecoded:1,packetsReceived:decoded*10}]]);
 pc.ontrack({track:{kind:'video'},streams:[{}],receiver:{}});v.emit();
 t.mock.timers.tick(1000);await flush();s.stop.interact();
 decoded++;v.decoded++;t.mock.timers.tick(100); // Watchdog sees the old pixels.
 pixels=new Uint8ClampedArray(24*48*4).fill(255);
 v.currentTime++;v.callback(performance.now(),{presentedFrames:2});
 for(let i=0;i<4;i++){t.mock.timers.tick(1000);await flush();}
 assert.equal(s.snapshots.length,0);assert.equal(pc.closed,undefined);assert.equal(s.modes.at(-1),'webrtc');
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

test('detail expires even if a small remote update escapes motion detection',async t=>{
  const s=setup(t,true,{mounted:true}),v=s.videos[0];
  t.mock.method(globalThis,'createImageBitmap',async()=>({width:720,height:1592,close(){}}));
  s.peers[0].ontrack({track:{kind:'video'},streams:[{}],receiver:{}});v.emit();
  t.mock.timers.tick(11000);s.snapshots[0].resolve(new Blob(['still']));await flush();
  assert.equal(s.details.at(-1),true);t.mock.timers.tick(2000);assert.equal(s.details.at(-1),false);
});

test('connected hardware profile without a first frame falls to a lower profile instead of retrying it',async t=>{
  const profiles=[],s=setup(t,true,{preference:{resolution:480,fps:10},profile:p=>profiles.push(p)});
  s.peers[0].connectionState='connected';t.mock.timers.tick(12000);t.mock.timers.tick(1000);
  assert.deepEqual(profiles,[{short:480,fps:10},{short:360,fps:10}]);
  s.videos[1].emit();t.mock.timers.tick(30000);
  assert.equal(s.peers.length,2);assert.equal(s.modes.at(-1),'webrtc');
});

test('offline first-frame timeout does not mark the requested profile incompatible',t=>{
  const profiles=[],s=setup(t,true,{preference:{resolution:480,fps:10},profile:p=>profiles.push(p)});
  t.mock.timers.tick(12000);t.mock.timers.tick(1000);
  assert.deepEqual(profiles,[{short:480,fps:10},{short:480,fps:10}]);
});
