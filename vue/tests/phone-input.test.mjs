import test from 'node:test';
import assert from 'node:assert/strict';
import { createPhoneInput, androidKey } from '../src/lib/phone-input.ts';
const flush=()=>new Promise(r=>setImmediate(r));
test('Chinese/English batch is pasted once, then backspace is ordered after its acknowledgement',async t=>{
 t.mock.timers.enable({apis:['setTimeout']}); const calls=[];let ack;
 const input=createPhoneInput(async(c,d)=>{calls.push([c,d]);if(c==='clipboard/set')await new Promise(r=>ack=r)},()=>assert.fail());
 input.insert('中文');input.insert('abc');input.key(67);await flush();
 assert.deepEqual(calls,[['clipboard/set',['中文abc']]]);ack();await flush();
 assert.deepEqual(calls.slice(1),[['device/input/keycode',[0,279]],['device/input/keycode',[0,67,0]]]);assert.equal(input.busy,false);
 input.reset();
});
test('disconnect cancels queued text and paste after an outstanding clipboard request',async()=>{
 const calls=[];let ack;
 const input=createPhoneInput(async(c,d)=>{calls.push([c,d]);await new Promise(r=>ack=r)},()=>{});
 input.insert('draft');input.flush();input.key(66);await flush();input.reset();ack();await flush();
 assert.equal(calls.length,1);assert.equal(input.busy,false);
});
test('failed clipboard acknowledgement never sends paste or queued Enter',async()=>{
 const calls=[];let errors=0;
 const input=createPhoneInput(async(c)=>{calls.push(c);throw Error('offline')},()=>errors++);
 input.insert('draft');input.key(66);await flush();assert.deepEqual(calls,['clipboard/set']);assert.equal(errors,1);
});
test('IME and paste shortcuts are not forwarded twice; editing keys are mapped',()=>{
 const event={key:'Enter',isComposing:true};assert.equal(androidKey(event),null);
 assert.equal(androidKey({key:'v',ctrlKey:true}),null);
 assert.equal(androidKey({key:'a'}),null);
 assert.deepEqual(androidKey({key:'Backspace'}),[67,0]);
 assert.deepEqual(androidKey({key:'a',ctrlKey:true}),[29,28672]);
});
test('copy reads selected phone text after Ctrl+C, then writes it to computer clipboard',async()=>{
 const calls=[];const input=createPhoneInput(async(c,d)=>{calls.push([c,d]);return c==='clipboard/get'?'复制测试':true},()=>assert.fail(),async text=>calls.push(['computer',text]));
 input.copy();await flush();
 assert.deepEqual(calls,[['device/input/keycode',[0,278]],['clipboard/get',[]],['computer','复制测试']]);
});
test('disconnect during clipboard fetch cannot overwrite the computer clipboard later',async()=>{
 let ack;const writes=[];
 const input=createPhoneInput(async c=>c==='clipboard/get'?new Promise(r=>ack=r):true,()=>{},async x=>writes.push(x));
 input.copy();await flush();input.reset();ack('stale');await flush();assert.deepEqual(writes,[]);
});
