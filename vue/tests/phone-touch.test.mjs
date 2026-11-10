import test from 'node:test';
import assert from 'node:assert/strict';
import { createPhoneTouch } from '../src/lib/phone-touch.ts';

function setup(t) {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  const sockets = [], states = [], routes = [];
  let lost = 0;
  class Socket {
    static OPEN = 1;
    constructor() { this.readyState = 0; this.bufferedAmount = 0; this.sent = []; sockets.push(this); }
    send(data) { if (this.readyState !== 1) throw Error('closed'); this.sent.push(JSON.parse(data)); }
    open() { this.readyState = 1; this.onopen?.(); this.receive({ type: 'OK', w: 1080, h: 2388 }); }
    receive(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
    close() { this.readyState = 3; this.onclose?.(); }
  }
  class Channel {
    constructor() { this.label = 'bk-touch-v1'; this.protocol = 'bk.touch.v1'; this.ordered = true; this.readyState = 'connecting'; this.bufferedAmount = 0; this.sent = []; }
    send(data) { if (this.readyState !== 'open') throw Error('closed'); this.sent.push(JSON.parse(data)); }
    open() { this.readyState = 'open'; this.onopen?.(); }
    receive(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
    close() { this.readyState = 'closed'; this.onclose?.(); }
  }
  const ws = globalThis.WebSocket, perf = Object.getOwnPropertyDescriptor(globalThis, 'performance');
  globalThis.WebSocket = Socket;
  Object.defineProperty(globalThis, 'performance', { configurable: true, value: { now: () => Date.now() } });
  const touch = createPhoneTouch({ url: '/touch', ready: r => states.push(r), lost: () => lost++, log() {}, stats: r => routes.push(r) });
  t.after(() => { touch.close(); globalThis.WebSocket = ws; Object.defineProperty(globalThis, 'performance', perf); });
  const channel = () => { const dc = new Channel(); touch.attach(dc); dc.open(); return dc; };
  const activate = () => { const dc = channel(); dc.receive({ type: 'capability', version: 1 }); dc.receive({ type: 'ready', w: 1080, h: 2388 }); return dc; };
  return { touch, sockets, routes, states, channel, activate, Channel, get lost() { return lost; } };
}
const down = { action: 'down', id: 0, x: 10, y: 20 }, up = { action: 'up', id: 0 };

test('old phones keep WSS when data channel lacks BK capability', t => {
  const s = setup(t); s.sockets[0].open(); const dc = s.channel();
  t.mock.timers.tick(3000);
  assert.equal(dc.readyState, 'closed'); assert.equal(s.routes.at(-1), 'wss');
  assert.equal(s.touch.send(down), true); s.touch.send(up);
  assert.equal(s.sockets[0].sent.filter(m => m.action === 'down').length, 1);
});

test('P2P claims only after current WSS gesture is released', t => {
  const s = setup(t); s.sockets[0].open(); s.touch.send(down);
  const dc = s.channel(); dc.receive({ type: 'capability', version: 1 });
  assert.equal(dc.sent.length, 0);
  s.touch.send(up); assert.equal(dc.sent[0].type, 'claim'); assert.equal(s.sockets[0].readyState, 3);
  dc.receive({ type: 'ready', w: 1080, h: 2388 }); s.touch.send(down); s.touch.send(up);
  assert.deepEqual(dc.sent.filter(x => x.type === 'touch').map(x => [x.seq, x.action]), [[1, 'down'], [2, 'up']]);
});

test('failed P2P gesture is not replayed through fallback', t => {
  const s = setup(t); s.sockets[0].open(); const dc = s.activate(); s.touch.send(down); dc.close();
  assert.equal(s.touch.send(up), false); assert.equal(s.states.at(-1), false);
  t.mock.timers.tick(2700); s.sockets[1].open();
  assert.deepEqual(s.sockets[1].sent, [{ action: 'reset' }]);
  assert.equal(s.touch.send({ action: 'move', id: 0, x: 30, y: 30 }), false);
  assert.equal(s.touch.send(down), true);
});

test('congested channel aborts without sending duplicate or delayed down', t => {
  const s = setup(t); const dc = s.activate(); dc.bufferedAmount = 9000;
  assert.equal(s.touch.send(down), false); assert.equal(dc.sent.filter(x => x.type === 'touch').length, 0);
  assert.equal(s.states.at(-1), false);
});

test('command recovery cannot bypass the previous P2P contact lease', t => {
  const s = setup(t); const dc = s.activate(); s.touch.send(down); dc.close();
  s.touch.recover(); t.mock.timers.tick(2000);
  assert.equal(s.sockets.length, 1);
  t.mock.timers.tick(700); assert.equal(s.sockets.length, 2);
});

test('missing application acknowledgements expires a held gesture', t => {
  const s = setup(t); const dc = s.activate(); s.touch.send(down);
  dc.receive({ type: 'pong', id: Date.now() }); t.mock.timers.tick(1500);
  assert.equal(dc.readyState, 'closed'); assert.equal(s.states.at(-1), false);
});

test('idle timer throttling preserves P2P but a subsequent unacknowledged contact still expires',t=>{
  const s=setup(t),dc=s.activate();t.mock.timers.tick(10000);
  assert.equal(dc.readyState,'open');assert.equal(s.routes.at(-1),'p2p');
  dc.receive({type:'pong',id:Date.now()});assert.equal(s.touch.send(down),true);
  t.mock.timers.tick(1500);assert.equal(dc.readyState,'closed');
});

test('slow ICE does not prematurely kill a connecting channel', t => {
  const s = setup(t); const dc = new s.Channel(); s.touch.attach(dc);
  t.mock.timers.tick(6000); assert.equal(dc.readyState, 'connecting');
  dc.open(); dc.receive({ type: 'capability', version: 1 });
  assert.equal(dc.sent[0].type, 'claim');
});

test('disposal cancels both routes and late readiness cannot reconnect', t => {
  const s = setup(t); const dc = s.activate(); s.touch.send(down); s.touch.close();
  dc.receive({ type: 'ready', w: 1080, h: 2388 }); t.mock.timers.tick(10000);
  assert.equal(s.sockets.length, 1); assert.equal(s.touch.send(down), false);
});
