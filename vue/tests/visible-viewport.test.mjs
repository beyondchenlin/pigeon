import test from 'node:test';
import assert from 'node:assert/strict';
import { viewportBounds, observeViewport } from '../src/lib/visible-viewport.ts';

test('keyboard resize/pan with unchanged layout viewport leaves the visible bottom clear', () => {
  const bounds = viewportBounds(390, 844, { width:390, height:430, offsetTop:72, offsetLeft:0 });
  assert.deepEqual(bounds, { width:390, height:430, top:72, left:0, bottom:342, right:0 });
  assert.equal(844 - bounds.bottom, bounds.top + bounds.height);
  assert.deepEqual(viewportBounds(390, 430), { width:390, height:430, top:0, left:0, bottom:0, right:0 });
});

test('browser bars, orientation and unavailable/invalid visual viewport have bounded fallbacks', () => {
  for (const [w,h,vh] of [[390,844,690],[390,844,780],[844,390,282]]) {
    const b = viewportBounds(w,h,{width:w,height:vh,offsetTop:0,offsetLeft:0});
    assert.equal(b.height + b.bottom,h); assert.equal(b.width,w);
  }
  assert.equal(viewportBounds(390,690,{width:0,height:NaN,offsetTop:0,offsetLeft:0}).height,690);
  assert.equal(viewportBounds(390,690,{width:390,height:700,offsetTop:-3,offsetLeft:-3}).bottom,0);
});

test('coalesces keyboard/toolbar events, follows visual pan, preserves pinch zoom and removes listeners', () => {
  const win = Object.assign(new EventTarget(), {innerWidth:390, innerHeight:844, document:new EventTarget()});
  win.visualViewport = Object.assign(new EventTarget(), {width:390,height:690,offsetTop:0,offsetLeft:0,scale:1});
  let next = 0; const frames = new Map(), samples = [];
  win.requestAnimationFrame = cb => { frames.set(++next,cb); return next; };
  win.cancelAnimationFrame = id => frames.delete(id);
  const flush = () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(cb => cb()); };
  const stop = observeViewport(win, b => samples.push(b));
  assert.equal(samples.length,1);
  Object.assign(win.visualViewport,{height:400,offsetTop:60});
  win.visualViewport.dispatchEvent(new Event('resize')); win.visualViewport.dispatchEvent(new Event('scroll')); win.dispatchEvent(new Event('resize'));
  assert.equal(frames.size,1); flush(); assert.equal(samples.at(-1).bottom,384);
  win.visualViewport.offsetTop=80; win.visualViewport.dispatchEvent(new Event('scroll')); flush(); assert.equal(samples.at(-1).top,80);
  win.visualViewport.scale=2; win.visualViewport.height=200; win.visualViewport.dispatchEvent(new Event('resize')); flush(); assert.equal(samples.at(-1).height,400);
  Object.assign(win.visualViewport,{scale:1,height:844,offsetTop:0}); win.document.dispatchEvent(new Event('fullscreenchange')); flush(); assert.equal(samples.at(-1).bottom,0);
  win.dispatchEvent(new Event('pageshow')); assert.equal(frames.size,1); stop(); assert.equal(frames.size,0);
  for (const type of ['scroll','resize']) win.visualViewport.dispatchEvent(new Event(type));
  win.dispatchEvent(new Event('resize')); win.dispatchEvent(new Event('pageshow')); win.document.dispatchEvent(new Event('fullscreenchange'));
  assert.equal(frames.size,0);
});
