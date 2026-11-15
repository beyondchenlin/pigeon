import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, sep, extname } from 'node:path';
import { spawn } from 'node:child_process';
const dist=resolve(process.argv[2]||'dist');
const chrome=['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe','C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe','/usr/bin/chromium'].find(existsSync);
assert.ok(chrome && existsSync(join(dist,'index.html')));
const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAABQAAAAoCAIAAABxU02MAAAAPklEQVR4nO3QoREAMAzDwNT7zxncEQprqrDcWfyJTt+uaRrLCsaJk18wTBR4wTBR4AXDRIEXDBMFXjBs6bAHksUDDN+Dv2gAAAAASUVORK5CYII=','base64');
const receipt = {
  task_id: '12345678-1234-4123-8123-123456789abc',
  goal: '单机测试任务', status: 'running', created_at: 1700000000,
};
const requests = [];
async function handleApi(req, path) {
  let body = '';
  for await (const chunk of req) body += chunk;
  requests.push({ path, method: req.method, body: body && JSON.parse(body) });
  switch (path) {
    case '/api/v1/device': return { total:1, data:[{ domain:'phonea', state:'online', model:'测试手机' }] };
    case '/api/v1/device/phonea': return { domain:'phonea', state:'online', model:'测试手机' };
    case '/api/v1/device/phonea/status':
      return [1, 2, 3].map(i => ({ timestamp:1700000000+i, cpu_percent:i, mem_percent:60 }));
    case '/api/v1/ai/models': return { user_id:1, models:[{ id:1, provider:'openai', name:'测试模型' }] };
    case '/api/v1/ai/jobs': return { jobs:[] };
    case '/api/v1/ai/chat/plan': return { clarification:'请补充任务说明' };
    case '/api/v1/device/phonea/ai/tasks': return { tasks:[receipt] };
    case '/api/v1/device/phonea/ai/tasks/' + receipt.task_id + '/cancel':
      receipt.cancel_requested = true;
      return receipt;
    case '/api/v1/device/phonea/ai/evidence':
      return { sync:{ state:'connected' }, evidence:[receipt.task_id, 'other'].map((id, i) => ({
        evidence_id:String(i), task_id:id, note:'归档样本' + i,
        phase:'terminal', captured_at_ms:1700000000000, size:0,
      })) };
    default: return {};
  }
}
const server=createServer(async (req,res)=>{
 const u=new URL(req.url,'http://localhost');
 if(u.pathname.endsWith('/frame')){requests.push({path:u.pathname,method:req.method});res.writeHead(200,{'Content-Type':'image/png'});res.end(pixel);return;}
 if(u.pathname.startsWith('/api/')){
   const data = await handleApi(req, u.pathname);
   res.writeHead(200,{'Content-Type':'application/json'});
   res.end(JSON.stringify({status:0,data}));
   return;
 }
 const file=resolve(dist,'.'+(u.pathname==='/'?'/index.html':decodeURIComponent(u.pathname)));if(!file.startsWith(dist+sep)||!existsSync(file)){res.writeHead(404);res.end();return;}const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css'};res.writeHead(200,{'Content-Type':mime[extname(file)]||'application/octet-stream'});res.end(readFileSync(file));});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;const profile=await mkdtemp(join(tmpdir(),'bk-touch-test-'));let browser,socket,session,seq=0;const pending=new Map();const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function call(method,params={},sid=session){const id=++seq;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('CDP timeout: '+method)),15000);pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v)},reject:e=>{clearTimeout(timer);reject(e)}});socket.send(JSON.stringify({id,method,params,...(sid?{sessionId:sid}:{})}));});}
async function ev(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
async function wait(expr){const end=Date.now()+15000;while(Date.now()<end){if(await ev(expr))return;await delay(50);}throw Error('Timed out '+expr+' state='+JSON.stringify(await ev(`({text:document.body.innerText.slice(0,1200),view:document.querySelector('.screen-viewport')?.outerHTML.slice(0,500),sent:window.sentTouches})`)));}
try {
 browser=spawn(chrome,['--headless=new','--disable-gpu','--no-first-run','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:['ignore','ignore','pipe']});
 const endpoint=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('Chrome timeout')),15000);browser.stderr.on('data',d=>{out+=d;const m=out.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m){clearTimeout(timer);resolve(m[1]);}});browser.once('error',reject);});
 socket=new WebSocket(endpoint);await new Promise((r,j)=>{socket.addEventListener('open',r,{once:true});socket.addEventListener('error',j,{once:true})});socket.addEventListener('message',e=>{const v=JSON.parse(e.data),p=pending.get(v.id);if(!p)return;pending.delete(v.id);v.error?p.reject(Error(v.error.message)):p.resolve(v.result)});
 const target=await call('Target.createTarget',{url:'about:blank'},null);session=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true},null)).sessionId;await call('Page.enable');await call('Runtime.enable');
 await call('Page.addScriptToEvaluateOnNewDocument',{source:`window.sentTouches=[]; window.openedSockets=[]; class MockWS { static OPEN=1; constructor(url){window.openedSockets.push(url);this.url=url;this.readyState=0;this.bufferedAmount=0;setTimeout(()=>{this.readyState=1;this.onopen?.({});if(url.includes('/ws/command')) this.onmessage?.({data:JSON.stringify({type:'status',data:{}})});},20)} send(s){if(s==='status' && this.url.includes('/ws/command')) this.onmessage?.({data:JSON.stringify({type:'status',data:{cpu_percent:5,mem_percent:60}})});if(this.url.includes('/ws/screen/touch')){const v=JSON.parse(s);window.sentTouches.push(v);if(v.action==='reset')setTimeout(()=>this.onmessage?.({data:JSON.stringify({type:'OK',w:1080,h:2388})}),20)}} close(){this.readyState=3}};window.WebSocket=MockWS;`});
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});await call('Page.navigate',{url:base+'/?transport=ws#/control/phonea'});
 await wait(`document.querySelector('[data-control-state="live"]') && document.querySelector('[data-input-transport="wss"]')`);
 assert.equal(await ev(`document.querySelector('[role=tab][aria-selected=true]').id`),'tab-evidence');
 const frameSize = await ev(`(()=>{const r=document.querySelector('canvas[aria-label="手机实时远控画面"]').getBoundingClientRect();return {width:r.width,height:r.height}})()`);
 assert.ok(Math.abs(frameSize.width / frameSize.height - 20 / 40) < 0.01);
 assert.ok(frameSize.height > 500);
 await call('Emulation.setDeviceMetricsOverride',{width:3840,height:2010,deviceScaleFactor:1,mobile:false});
 await wait(`document.querySelector('.phone-navigation').getBoundingClientRect().width > 440`);
 const widths = await ev(`(()=>({screen:document.querySelector('canvas[aria-label="手机实时远控画面"]').getBoundingClientRect().width, nav:document.querySelector('.phone-navigation').getBoundingClientRect().width}))()`);
 assert.ok(Math.abs(widths.nav - widths.screen - 12) < 1);
 await call('Emulation.setDeviceMetricsOverride',{width:768,height:1024,deviceScaleFactor:1,mobile:false});
 await wait(`Math.abs(document.querySelector('.phone-navigation').getBoundingClientRect().width - document.querySelector('canvas[aria-label="手机实时远控画面"]').getBoundingClientRect().width - 12) < 1`);
 await call('Emulation.setDeviceMetricsOverride',{width:320,height:568,deviceScaleFactor:1,mobile:true});
 await wait(`Math.abs(document.querySelector('.phone-navigation').getBoundingClientRect().width - document.querySelector('canvas[aria-label="手机实时远控画面"]').getBoundingClientRect().width) < 1`);
 await call('Emulation.setDeviceMetricsOverride',{width:844,height:390,deviceScaleFactor:1,mobile:false});
 await wait(`document.querySelector('canvas[aria-label="手机实时远控画面"]').getBoundingClientRect().height >= 200`);
 assert.ok(await ev(`Array.from(document.querySelectorAll('.phone-navigation button')).every(b => b.getBoundingClientRect().width >= 40)`));
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
 await wait(`document.querySelector('canvas[aria-label="手机实时远控画面"]').getBoundingClientRect().height < 900`);
 const rect=await ev(`(()=>{const r=document.querySelector('canvas[aria-label="手机实时远控画面"]').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...rect});await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...rect});
 await wait(`window.sentTouches.some(x=>x.action==='up')`);assert.deepEqual(await ev(`window.sentTouches.filter(x=>x.action==='down'||x.action==='up').map(x=>x.action)`),['down','up']);assert.equal(await ev(`document.querySelector('.device-workbench').classList.contains('workbench-clean')`),false);
 await ev(`document.querySelector('button[aria-label="进入纯画面"]').click()`);assert.equal(await ev(`document.querySelector('.device-workbench').classList.contains('workbench-clean')`),true);await ev(`document.querySelector('button[aria-label="退出纯画面"]').click()`);assert.equal(await ev(`document.querySelector('.device-workbench').classList.contains('workbench-clean')`),false);
 await ev(`window.initialCanvas = document.querySelector('canvas[aria-label="手机实时远控画面"]'); true`);
 async function clickTab(name) {
   await ev(`document.getElementById('tab-' + ${JSON.stringify(name)}).click()`);
   await wait(`document.getElementById('panel-' + ${JSON.stringify(name)})?.getBoundingClientRect().height > 0`);
 }
 async function checkPhoneTouch() {
   assert.ok(await ev(`window.initialCanvas === document.querySelector('canvas[aria-label="手机实时远控画面"]')`));
   const before = await ev('window.sentTouches.filter(x => x.action === "up").length');
   const current = await ev(`(()=>{const r=window.initialCanvas.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
   await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...current});
   await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...current});
   await wait(`window.sentTouches.filter(x => x.action === 'up').length === ${before + 1}`);
 }
 async function clickText(panel, label) {
   const match = `Array.from(document.querySelectorAll('${panel} button')).find(b => b.offsetWidth && b.textContent.includes(${JSON.stringify(label)}))`;
   await wait(match + ' != null');
   await ev(match + '.click()');
 }
 async function checkToolbar() {
   const layout = await ev(`(() => {
     const row = document.querySelector('.details-toolbar');
     const tabs = Array.from(row.querySelectorAll('[role="tab"]'));
     const tools = row.lastElementChild;
     const lastTab = tabs.at(-1).getBoundingClientRect();
     const button = tools.getBoundingClientRect();
     return {
       labels: [...tabs, tools].map(el => el.textContent.trim()),
       sameRow: lastTab.right <= button.left && button.top < lastTab.bottom && button.bottom > lastTab.top,
       overflows: row.scrollWidth > row.clientWidth
     };
   })()`);
   assert.deepEqual(layout.labels, ['设备属性', '性能', '文件', '连接日志', 'AI 任务', '任务证据', '应用与更多工具']);
   assert.ok(layout.sameRow);
   return layout;
 }
 assert.equal((await checkToolbar()).overflows, false);
 await ev(`document.querySelector('button[aria-label="打开远控工具"]').click()`);
 await clickText('.tools-dialog','设备属性与文件');
 await wait(`!document.querySelector('.tools-dialog')`);
 assert.equal(await ev(`document.querySelector('[role=tab][aria-selected=true]').id`),'tab-properties');
 await clickTab('performance');
 await wait(`document.querySelectorAll('.performance-charts div[_echarts_instance_]').length === 3`);
 assert.equal(await ev(`document.querySelectorAll('.performance-charts canvas').length`), 6);
 await checkPhoneTouch();
 await clickTab('ai');
 await wait(`document.querySelector('#panel-ai').innerText.includes('当前设备：phonea')`);
 const draft = '检查这台手机的状态';
 await ev(`const input=document.querySelector('#panel-ai textarea'); input.value=${JSON.stringify(draft)}; input.dispatchEvent(new Event('input',{bubbles:true}))`);
 await clickTab('properties');
 await clickTab('ai');
 assert.equal(await ev(`document.querySelector('#panel-ai textarea').value`),draft);
 await checkPhoneTouch();
 await wait(`!document.querySelector('#panel-ai button[type="submit"]').disabled`);
 await clickText('#panel-ai','发送');
 await wait(`document.querySelector('#panel-ai').innerText.includes('请补充任务说明')`);
 assert.deepEqual(requests.find(r=>r.path.endsWith('/chat/plan')).body.selected_target, {kind:'device',name:'phonea'});
 await clickText('#panel-ai aside',receipt.goal);
 await clickText('#panel-ai','停止任务');
 await wait(`document.querySelector('#panel-ai').innerText.includes('再次请求停止')`);
 assert.ok(requests.some(r => r.method === 'POST' && r.path === '/api/v1/device/phonea/ai/tasks/' + receipt.task_id + '/cancel'));
 await clickText('#panel-ai','查看本次证据');
 await wait(`document.querySelector('#panel-evidence')?.innerText.includes('归档样本0')`);
 assert.ok(!(await ev(`document.querySelector('#panel-evidence').innerText.includes('归档样本1')`)));
 await clickText('#panel-evidence','查看全部证据');
 await wait(`document.querySelector('#panel-evidence').innerText.includes('归档样本1')`);
 await checkPhoneTouch();
 assert.equal(await ev(`window.openedSockets.filter(url=>url.includes('/ws/command')).length`),1);
 await call('Emulation.setDeviceMetricsOverride',{width:360,height:780,deviceScaleFactor:1,mobile:true});
 await wait(`document.querySelector('dialog.workbench-details')`);
 await ev(`document.querySelector('button[aria-label="打开远控工具"]').click()`);
 await clickText('.tools-dialog','设备属性与文件');
 await wait(`document.querySelector('dialog.workbench-details[open]')`);
 assert.equal(await ev(`document.querySelector('[role=tab][aria-selected=true]').id`),'tab-properties');
 assert.equal((await checkToolbar()).overflows, true);
 await ev(`document.querySelector('.details-toolbar > button').scrollIntoView({inline:'end',block:'nearest'})`);
 await wait(`document.querySelector('.details-toolbar').scrollLeft > 0`);
 await clickText('.details-toolbar','应用与更多工具');
 await wait(`!document.querySelector('dialog.workbench-details[open]') && document.querySelector('.tools-dialog')?.getBoundingClientRect().width > 0`);
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
 const framesBeforeList = requests.filter(r=>r.path.endsWith('/frame')).length;
 await call('Page.navigate',{url:base+'/#/devices'});
 await wait(`document.querySelector('[data-device-card="phonea"]')`);
 await delay(1500);
 assert.equal(requests.filter(r=>r.path.endsWith('/frame')).length, framesBeforeList);
 assert.ok(!(await ev(`window.openedSockets.some(url=>url.includes('/ws/screen'))`)));
 console.log('PASS: workbench charts, current-device chat, draft, history, stop, scoped evidence and uninterrupted phone touch');
} finally {socket?.close();if(browser){browser.kill();await new Promise(r=>browser.once('exit',r));}await new Promise(r=>server.close(r));await rm(profile,{recursive:true,force:true,maxRetries:3,retryDelay:100});}
