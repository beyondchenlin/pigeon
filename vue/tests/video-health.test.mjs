import test from 'node:test';
import assert from 'node:assert/strict';
import {VideoHealth} from '../src/lib/video-health.ts';

test('a static idle phone is never treated as stalled merely for not producing frames',()=>{
 const h=new VideoHealth();h.frame(100);
 for(let now=1000;now<120000;now+=1000)assert.equal(h.sample(now,1,10,-Infinity),null);
});

test('an interaction without a new decoded frame probes once, not every timer tick',()=>{
 const h=new VideoHealth();h.frame(100);h.sample(1000,1,10,-Infinity);
 assert.equal(h.sample(2000,1,10,1500),null);
 assert.equal(h.sample(3000,1,10,1500),'操作后视频未更新');
 assert.equal(h.sample(4000,1,10,1500),null);
 h.frame(4100);assert.equal(h.sample(5000,2,20,1500),null);
 assert.equal(h.sample(7000,2,20,5100),'操作后视频未更新');
});

test('RTP without decoded progress is stalled; new decoded progress and sparse idle traffic are safe',()=>{
 const h=new VideoHealth();h.frame(100);h.sample(1000,1,10,-Infinity);
 assert.equal(h.sample(2000,1,20,-Infinity),null);
 assert.equal(h.sample(4000,1,30,-Infinity),'视频仍在收包但未解码出帧');
 assert.equal(h.sample(5000,2,40,-Infinity),null);
 assert.equal(h.sample(10000,2,40,-Infinity),null);
});
