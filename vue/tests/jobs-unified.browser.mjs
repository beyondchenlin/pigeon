// Isolated browser test: synthetic API only; never sends real phone tasks.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, sep, extname } from 'node:path';
import { spawn } from 'node:child_process';

const dist = resolve(process.argv[2] || 'dist');
const chrome = process.env.CHROME_BIN || ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', '/usr/bin/chromium'].find(existsSync);
assert.ok(chrome && existsSync(join(dist, 'index.html')));
const calls = [];
const legacy = {id: 1, name:'旧脚本任务', mode:'loop', state:'running'};
const smart = {id: 7, name:'智能任务七', mode:'bk_managed', state:'bk_running'};
const job = {id:7, goal:'智能任务七', state:'bk_running', schedule:{kind:'now'}, next_at:null, devices:[]};
const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/')) {
    calls.push({path:url.pathname, method:req.method, filter:url.searchParams.get('filter')});
    let data = {};
    if (url.pathname === '/api/v1/job') data = {data:[legacy, smart], total:2};
    else if (url.pathname === '/api/v1/job/status') data = {running:1, paused:0, success:0, failed:0, timeout:0};
    else if (url.pathname === '/api/v1/ai/jobs-summary') data = {running:1, paused:0};
    else if (url.pathname === '/api/v1/ai/jobs') data = {jobs:[]}; // target is older than list window
    else if (url.pathname === '/api/v1/ai/jobs/7') data = job;
    else if (url.pathname === '/api/v1/ai/models') data = {user_id:1, models:[]};
    res.writeHead(200, {'Content-Type':'application/json'}); res.end(JSON.stringify({status:0,data})); return;
  }
  const pathname=decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname);
  const file=resolve(dist,'.'+pathname);
  if (!file.startsWith(dist+sep) || !existsSync(file)) {res.writeHead(404);res.end();return;}
  const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.ttf':'font/ttf'};
  res.writeHead(200, {'Content-Type':types[extname(file)] || 'application/octet-stream'});res.end(readFileSync(file));
});
await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
const base='http://127.0.0.1:'+server.address().port;
const profile=await mkdtemp(join(tmpdir(),'pigeon-unified-test-'));
let browser, socket, session, seq=0;
const pending=new Map();
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function call(method,params={},sessionId=session) {
  const id=++seq;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout '+method));},15000);
    pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
    socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));
  });
}
async function evalJs(expression) {
  const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(r.exceptionDetails) throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);
  return r.result.value;
}
async function waitFor(expr) {
  const end=Date.now()+12000;
  while(Date.now()<end){if(await evalJs(expr))return;await delay(50);}
  throw Error('Timed out: '+expr);
}
const has=text=>'document.body.innerText.includes('+JSON.stringify(text)+')';
async function click(text) {await evalJs(`(()=>{const e=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${JSON.stringify(text)});if(!e)throw Error('button missing');e.click()})()`);}
try {
  browser=spawn(chrome,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:['ignore','ignore','pipe']});
  const endpoint=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Chrome startup timeout')),15000);browser.once('error',reject);browser.stderr.on('data',data=>{output+=data;const m=output.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m){clearTimeout(timer);resolve(m[1]);}});});
  socket=new WebSocket(endpoint);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  socket.addEventListener('message',e=>{const v=JSON.parse(e.data),p=pending.get(v.id);if(!p)return;pending.delete(v.id);v.error?p.reject(Error(v.error.message)):p.resolve(v.result);});
  const target=await call('Target.createTarget',{url:'about:blank'},null);
  session=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true},null)).sessionId;
  await call('Page.enable');await call('Runtime.enable');
  await call('Page.navigate',{url:base+'/#/jobs'});await waitFor(has('智能任务七'));
  assert.equal(await evalJs(has('脚本 ·')),true);
  const pos=await evalJs('(()=>{const r=[...document.querySelectorAll("button")].find(x=>x.textContent.trim()==="全部状态").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()');
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...pos});
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...pos});
  await waitFor('[...document.querySelectorAll("[role=option]")].some(x=>x.textContent.trim()==="运行中")');
  const option=await evalJs('(()=>{const r=[...document.querySelectorAll("[role=option]")].find(x=>x.textContent.trim()==="运行中").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()');
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...option});
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...option});
  await waitFor('document.body.innerText.includes("运行中")');
  for(let i=0;i<100 && !calls.some(c=>c.filter?.includes('bk_finished'));i++) await delay(50);
  const runningFilter=calls.map(c=>c.filter).filter(Boolean).map(JSON.parse).flat().find(x=>x.field==='state');
  assert.deepEqual(runningFilter.value,['running','bk_running','bk_finished']);
  await click('新建任务');await waitFor(has('继续安排智能任务'));
  assert.equal(await evalJs('[...document.querySelectorAll("button[aria-pressed]")].find(x=>x.textContent.includes("BK 智能任务"))?.getAttribute("aria-pressed")'),'true');
  await evalJs('[...document.querySelectorAll("button")].find(x=>x.textContent.includes("脚本任务") && x.hasAttribute("aria-pressed")).click()');
  await waitFor('!document.body.innerText.includes("继续安排智能任务")');
  assert.ok(await evalJs('document.body.innerText.includes("脚本")'));
  await evalJs('[...document.querySelectorAll("button[aria-pressed]")].find(x=>x.textContent.includes("BK 智能任务")).click()');
  await click('继续安排智能任务');await waitFor(has('告诉手机做什么'));
  assert.equal(calls.filter(c=>c.method==='POST').length,0);
  await call('Page.navigate',{url:base+'/#/jobs'});await waitFor(has('智能任务七'));
  await evalJs('[...document.querySelectorAll("tr")].find(x=>x.textContent.includes("智能任务七")).click()');
  await waitFor(has('从任务列表打开的任务 #7'));
  assert.ok(calls.some(c=>c.path==='/api/v1/ai/jobs/7'));
  assert.equal(calls.filter(c=>c.method==='POST').length,0);
  console.log('PASS unified creation choice, preserved script route and exact BK task lookup');
} finally {
  if(socket?.readyState===1){try{await call('Browser.close',{},null);}catch{}socket.close();}
  if(browser)try{browser.kill();}catch{}
  server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:100}).catch(()=>{});
}
