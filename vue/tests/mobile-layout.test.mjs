import test from 'node:test';
import assert from 'node:assert/strict';
import { fitDisplay, galleryMediaHeight } from '../src/lib/mobile-layout.ts';
import { writePhoneText } from '../src/lib/phone-input.ts';

test('full phone frames fit portrait, landscape and unusual brands without cropping or stretching',()=>{
 for(const [w,h] of [[540,1194],[1080,1920],[2400,1080],[720,1600]])for(const [aw,ah] of [[390,844],[320,568],[844,390],[190,240]]){
  const r=fitDisplay(w,h,aw,ah);assert.ok(r.width<=aw+.001&&r.height<=ah+.001);assert.ok(Math.abs(r.width/r.height-w/h)<.00001);
 }
 assert.deepEqual(fitDisplay(0,0,390,844),{width:0,height:0});
});
test('two mobile card rows include their headers, footers and gap within available height',()=>{
 for(const height of [240,320,500,650]){const media=galleryMediaHeight(height,true,false);assert.ok((media+60)*2+8<=height);}
 assert.ok(galleryMediaHeight(500,true,true)>galleryMediaHeight(500,true,false));
});
test('mobile compose awaits clipboard acknowledgement and only pastes, never sends a message',async()=>{
 const calls=[];await writePhoneText(async(c,d)=>calls.push([c,d]),'中文\n第二行🙂',()=>true);
 assert.deepEqual(calls,[['clipboard/set',['中文\n第二行🙂']],['device/input/keycode',[0,279]]]);
});
test('device switch or disconnect between clipboard and paste cancels paste; failure is not retried',async()=>{
 let valid=true;const calls=[];
 await assert.rejects(writePhoneText(async c=>{calls.push(c);valid=false},'draft',()=>valid));assert.deepEqual(calls,['clipboard/set']);
 let count=0;await assert.rejects(writePhoneText(async()=>{count++;throw Error('timeout')},'draft',()=>true));assert.equal(count,1);
});
