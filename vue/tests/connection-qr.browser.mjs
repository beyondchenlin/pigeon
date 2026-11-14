// Synthetic browser acceptance for device QR configuration. No real credentials.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, extname, sep } from 'node:path';
import { spawn } from 'node:child_process';

const dist = resolve('dist');
const chrome = process.env.CHROME_BIN || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/chromium', '/usr/bin/google-chrome',
].find(existsSync);
assert.ok(chrome && existsSync(join(dist, 'index.html')));
const old = {domain:'oldphone', mode:'forward', state:'offline', comment:'原手机', model:'模拟手机'};
const created = {domain:'newphone', mode:'forward', state:'pending', token_id:'fake-device-token'};
let creates = 0;
const calls = [];
const reply = (res, data, code=200) => { res.writeHead(code, {'content-type':'application/json'}); res.end(JSON.stringify({status:code===200?0:code, data})); };
const server = createServer((req,res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/')) {
    calls.push([req.method,url.pathname]);
    if (url.pathname === '/api/v1/platform/config') return reply(res, {public_ip:'192.0.2.1',web_port:'18000',sapi_ckey:'synthetic-key'});
    if (url.pathname === '/api/v1/device' && req.method === 'POST') { creates++; return reply(res, created); }
    if (url.pathname === '/api/v1/device') return reply(res, {total:1,data:[old]});
    if (url.pathname === '/api/v1/group') return reply(res, {total:0,data:[]});
    if (url.pathname === '/api/v1/ungrouped/devices') return reply(res, {total:1,data:[old]});
    if (url.pathname === '/api/v1/summary') return reply(res, {total:1,offline:1});
    return reply(res, {});
  }
  const file = resolve(dist, '.' + (url.pathname === '/' ? '/index.html' : decodeURIComponent(url.pathname)));
  if (!file.startsWith(dist + sep) || !existsSync(file)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, {'content-type':({'.html':'text/html','.js':'text/javascript','.css':'text/css','.ttf':'font/ttf'}[extname(file)] || 'application/octet-stream')});
  res.end(readFileSync(file));
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = await mkdtemp(join(tmpdir(),'pigeon-qr-browser-'));
let browser, socket, session, sequence=0;
const pending = new Map();
const pause = ms=>new Promise(r=>setTimeout(r,ms));
async function call(method, params={}, sid=session) {
  const id=++sequence;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout'));},15000);
    pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});
    socket.send(JSON.stringify({id,method,params,...(sid?{sessionId:sid}:{})}));
  });
}
async function evalPage(expression) {
  const value=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if (value.exceptionDetails) throw Error(value.exceptionDetails.text);
  return value.result.value;
}
async function waitFor(expression) {
  for (let i=0;i<200;i++) { if(await evalPage(expression)) return; await pause(50); }
  throw Error('Browser condition timed out: '+expression);
}
const click = label=>evalPage(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${JSON.stringify(label)});if(!b||b.disabled)throw Error('button unavailable');b.click()})()`);
try {
  browser=spawn(chrome,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:['ignore','ignore','pipe']});
  const endpoint=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('browser timeout')),15000);browser.once('error',reject);browser.stderr.on('data',chunk=>{output+=chunk;const m=output.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m){clearTimeout(timer);resolve(m[1]);}});});
  socket=new WebSocket(endpoint); await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  socket.addEventListener('message',event=>{const data=JSON.parse(event.data), item=pending.get(data.id);if(!item)return;pending.delete(data.id);data.error?item.reject(Error(data.error.message)):item.resolve(data.result);});
  const target=await call('Target.createTarget',{url:'about:blank'},null);
  session=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true},null)).sessionId;
  await call('Page.enable'); await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:base+'/#/devices'});
  await waitFor("document.querySelector('[data-device-card=oldphone] input[type=checkbox]')");
  await evalPage("document.querySelector('[data-device-card=oldphone] input[type=checkbox]').click()");
  await waitFor("document.body.innerText.includes('接入配置二维码')");
  await click('接入配置二维码');
  await waitFor("document.querySelector('[role=dialog] canvas') && document.body.innerText.includes('properties.tries=64')");
  assert.equal(await evalPage("document.querySelector('[role=dialog] code').innerText.includes('/properties/oldphone')"),true);
  assert.equal(creates,0,'recovering a device must not create another device');
  const colorCount=await evalPage("(()=>{const c=document.querySelector('[role=dialog] canvas'),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let dark=0,light=0;for(let i=0;i<d.length;i+=4){if(d[i]<50)dark++;if(d[i]>240&&d[i+3]>0)light++}return {dark,light}})()");
  assert.ok(colorCount.dark>100 && colorCount.light>100,'QR must render dark and light modules');
  await click('关闭');
  await waitFor("!document.querySelector('[role=dialog] canvas')");
  await click('添加设备');
  await click('转发（扫码接入）');
  await click('确认添加');
  await waitFor("document.querySelector('[role=dialog] canvas') && document.body.innerText.includes('/properties/newphone')");
  assert.equal(creates,1);
  assert.ok(calls.filter(c=>c[1]==='/api/v1/platform/config').length>=2,'reopen must reacquire configuration');
  console.log('PASS: existing-device QR, cleanup, and new forward-device QR using synthetic data');
} finally {
  if(socket?.readyState===1){try{await call('Browser.close',{},null);}catch{}socket.close();}
  if(browser)browser.kill();
  server.closeAllConnections(); await new Promise(r=>server.close(r));
  await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:100}).catch(()=>{});
}
