// Runs against the production bundle and a loopback-only synthetic backend.
// No production server, phone, model, or browser profile is used.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import { mkdtemp, rm, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, extname, sep } from 'node:path';
import { spawn } from 'node:child_process';
import { prepareTask } from '../src/lib/task-request.mjs';

const dist = resolve(process.argv[2] || 'dist');
const artifacts = process.argv[3] ? resolve(process.argv[3]) : null;
const chrome = process.env.CHROME_BIN || [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  '/usr/bin/chromium', '/usr/bin/google-chrome'
].find(existsSync);
assert.ok(chrome, 'Set CHROME_BIN to an installed Chrome/Chromium executable');
assert.ok(existsSync(join(dist,'index.html')), 'Build the production bundle before running this test');
const state = { tasks:[], posts:0, cancels:0, answers:0, calls:[], loseNextPost:true,
  evidence:[], listOverride:null, delayedCancel:null, delayedPost:null, holdNextPost:false };
const now = () => Date.now()/1000;
const json = (response,data,status=200) => { response.writeHead(status,{'Content-Type':'application/json'}); response.end(JSON.stringify({status:status===200?0:status,data})); };
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS0AAAAASUVORK5CYII=','base64');
const server = createServer(async(request,response) => {
  try {
    const url = new URL(request.url,'http://127.0.0.1');
    if (url.pathname.startsWith('/api/')) {
      state.calls.push({method:request.method,path:url.pathname,refresh:url.searchParams.get('refresh')});
      if(url.pathname==='/api/v1/user/login') return json(response,{});
      const match=url.pathname.match(/^\/api\/v1\/device\/([a-z0-9]+)\/ai\/(tasks|evidence)(?:\/([^/]+)(?:\/([^/]+))?)?$/);
      if(!match) return json(response,{},404);
      const [,domain,kind,id,action]=match;
      if(kind==='evidence') {
        if(action==='image') { response.writeHead(200,{'Content-Type':'image/png'});return response.end(png); }
        return json(response,{evidence:state.evidence,sync:{state:'connected'}});
      }
      if(request.method==='GET') {
        if(!id) return json(response,{tasks:state.listOverride || state.tasks.filter(task=>task.domain===domain)});
        const row=state.tasks.find(task=>task.task_id===id && task.domain===domain);
        return row?json(response,row):json(response,{},404);
      }
      let data='';for await(const chunk of request)data+=chunk;
      const body=JSON.parse(data||'{}');
      if(!id) {
        state.posts++;
        const prepared=await prepareTask(domain,body.goal,body.request_id,{timeout_s:body.timeout_s,...(body.execution?{execution:body.execution}:{})});
        const row={domain,task_id:prepared.taskId,request_id:body.request_id,goal:body.goal,status:'running',
          execution:body.execution,created_at:now(),updated_at:now(),deadline:now()+body.timeout_s,
          phone_received_at:now(),source_event_at_ms:Date.now(),result:{progress:'正在浏览',step:0}};
        state.tasks.unshift(row);
        if(state.holdNextPost){state.holdNextPost=false;state.delayedPost=()=>json(response,row);return;}
        if(state.loseNextPost){state.loseNextPost=false;response.writeHead(504,{'Content-Type':'application/json'});response.end(JSON.stringify({status:504,error:'SIMULATED_REPLY_LOST'}));return;}
        return json(response,row);
      }
      const row=state.tasks.find(task=>task.task_id===id&&task.domain===domain);
      if(!row)return json(response,{},404);
      if(action==='cancel') {
        state.cancels++;row.cancel_requested=true;row.updated_at=now();
        state.delayedCancel=()=>json(response,row);return;
      }
      if(action==='answer') {
        state.answers++;row.status='running';row.updated_at=now();
        row.answer_submission={request_id:body.request_id,status:'accepted'};
        return json(response,row);
      }
      return json(response,{},404);
    }
    const pathname=decodeURIComponent(url.pathname==='/'?'/index.html':url.pathname);
    const file=resolve(dist,'.'+pathname);
    if(!file.startsWith(dist+sep)||!existsSync(file)){response.writeHead(404);return response.end();}
    const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.ttf':'font/ttf'};
    response.writeHead(200,{'Content-Type':types[extname(file)]||'application/octet-stream'});
    response.end(readFileSync(file));
  } catch(error) {json(response,{error:String(error)},500);}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base='http://127.0.0.1:'+server.address().port;
const profile=await mkdtemp(join(tmpdir(),'pigeon-web-test-'));
let browser, socket, sequence=0, session;
const pending=new Map(), passes=[];
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function call(method,params={},sessionId=session) {
  const id=++sequence;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout: '+method));},15000);
    pending.set(id,{resolve:result=>{clearTimeout(timer);resolve(result);},reject:error=>{clearTimeout(timer);reject(error);}});
    socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));
  });
}
async function evaluate(expression) {
  const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(result.exceptionDetails)throw Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);
  return result.result.value;
}
async function waitFor(expression,timeout=12000) {
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline){if(await evaluate(expression))return;await pause(50);}
  throw Error('Browser condition timed out: '+expression);
}
const textIncludes=text=>'document.body.innerText.includes('+JSON.stringify(text)+')';
const fill=(selector,value)=>evaluate('(()=>{const e=document.querySelector('+JSON.stringify(selector)+');if(!e)throw Error("Missing input");e.value='+JSON.stringify(String(value))+';e.dispatchEvent(new Event("input",{bubbles:true}));})()');
const clickText=text=>evaluate('(()=>{const b=[...document.querySelectorAll("button")].find(x=>x.textContent.trim()==='+JSON.stringify(text)+');if(!b||b.disabled)throw Error("Button unavailable: "+'+JSON.stringify(text)+');b.click();})()');
async function refresh(){await waitFor('![...document.querySelectorAll("button")].find(x=>x.textContent.trim()==="刷新状态")?.disabled');await clickText('刷新状态');await pause(150);}
async function navigate(hash){await call('Page.navigate',{url:base+'/#'+hash});await waitFor('document.querySelector("h1") !== null');}
function passed(name){passes.push(name);console.log('PASS '+name);}
try {
  browser=spawn(chrome,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
    '--disable-background-networking','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:['ignore','ignore','pipe']});
  const endpoint=await new Promise((resolve,reject)=>{
    let output='';const timer=setTimeout(()=>reject(Error('Chrome did not expose debugging endpoint')),15000);
    browser.once('error',error=>{clearTimeout(timer);reject(error);});
    browser.stderr.on('data',data=>{output+=data;const match=output.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
  });
  socket=new WebSocket(endpoint);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  socket.addEventListener('message',event=>{
    const data=JSON.parse(event.data),task=pending.get(data.id);if(!task)return;
    pending.delete(data.id);data.error?task.reject(Error(data.error.message)):task.resolve(data.result);
  });
  const target=await call('Target.createTarget',{url:'about:blank'},null);
  const attached=await call('Target.attachToTarget',{targetId:target.targetId,flatten:true},null);session=attached.sessionId;
  await call('Page.enable');await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
  await navigate('/ai/phonea');
  await waitFor(textIncludes('还没有任务'));
  await fill('#task-goal','只浏览，不互动');
  await evaluate('document.querySelector("input[type=checkbox]").click()');
  await pause(30);
  assert.equal(await evaluate('document.querySelector("form button[type=submit]").disabled'),true);
  await fill('#browse-budget',100000);await fill('#browse-observations',20);await fill('#browse-active',6000);await fill('#browse-stall',120);
  await waitFor('!document.querySelector("form button[type=submit]").disabled');
  await clickText('交给这台手机');
  await waitFor(textIncludes('手机执行中'));
  const browseRow=state.tasks[0], browseId=browseRow.task_id;
  assert.equal(state.posts,1);assert.equal(browseRow.execution.duration_s,7200);assert.equal(browseRow.execution.budget.max_tokens,100000);
  assert.ok(state.calls.some(c=>c.path.endsWith(browseId)&&c.refresh==='0'));
  passed('browse contract, lost response lookup and no repeat submission');
  await call('Page.reload');
  await waitFor(textIncludes('手机执行中'));
  assert.equal(state.posts,1);
  assert.ok(await evaluate('sessionStorage.getItem("bk-task-pending:phonea")!==null'));
  passed('reload restores original receipt without business replay');

  await clickText('停止任务');
  await waitFor(textIncludes('停止待确认'));
  assert.equal(await evaluate('document.querySelector("form button[type=submit]").disabled'),true);
  assert.equal(state.cancels,1);
  assert.equal(await evaluate('[...document.querySelectorAll("strong")].some(x=>x.textContent==="已停止")'),false);
  state.delayedCancel();state.delayedCancel=null;
  await waitFor('[...document.querySelectorAll("button")].some(x=>x.textContent.trim()==="再次请求停止"&&!x.disabled)');
  await fill('#task-goal','下一项仍应被阻止');
  assert.equal(await evaluate('document.querySelector("form button[type=submit]").disabled'),true);
  browseRow.status='cancelled';browseRow.updated_at=now();
  await refresh();await waitFor('[...document.querySelectorAll("strong")].some(x=>x.textContent==="已停止")');
  assert.equal(await evaluate('sessionStorage.getItem("bk-task-pending:phonea")'),null);
  assert.equal(await evaluate('document.querySelector("form button[type=submit]").disabled'),false);
  passed('stop intent is immediate; admission remains blocked until true terminal');

  state.listOverride=[{...browseRow,status:'running',updated_at:browseRow.updated_at-10,cancel_requested:false}];
  await refresh();
  assert.equal(await evaluate('[...document.querySelectorAll("strong")].some(x=>x.textContent==="已停止")'),true);
  state.listOverride=null;
  passed('late running snapshot cannot overwrite terminal state');

  const questionId='00000000-0000-4000-8000-000000000002';
  const question={domain:'phonea',task_id:questionId,request_id:questionId,goal:'合成追问任务',status:'waiting_for_answer',
    question_key:'question-1',created_at:now(),updated_at:now(),source_event_at_ms:null,phone_received_at:null,
    result:{question:'需要浏览哪个分类？',answerable:true,options:['科学']}};
  state.tasks=[question,browseRow];
  await refresh();await waitFor(textIncludes('需要浏览哪个分类'));
  assert.equal(await evaluate(textIncludes('1970')),false);
  await fill('#task-answer-'+questionId,'科学');
  await clickText('答复并继续原任务');await waitFor(textIncludes('手机执行中'));
  assert.equal(state.answers,1);assert.ok(question.answer_submission.request_id);
  passed('ordinary question reply stays attached to the original task');

  question.status='done';question.updated_at=now();
  browseRow.status='done';browseRow.cancel_requested=false;browseRow.updated_at=now()+1;
  const started=Date.now()-7200000, finished=Date.now();
  browseRow.result={run_id:'run-1',execution_result:{actual_started_at_ms:started,finished_at_ms:finished,last_progress_at_ms:finished-1000,
    elapsed_ms:7200000,active_ms:6600000,waiting_ms:300000,paused_ms:100000,stalled_ms:200000,observations:80,
    duration_reached:true,business_success:true,execution_finished:true,reason:'duration_elapsed',
    usage:{reported_tokens:24000,input_tokens:20000,output_tokens:4000,model_calls:50,unreported_calls:0},
    evidence:{state:'missing',reason:'synthetic_capture_failure'}}};
  await call('Page.reload');await waitFor(textIncludes('完整浏览验收未通过'));
  assert.ok(await evaluate(textIncludes('有效执行：6600秒')));
  const evidence={state:'captured',evidence_id:'00000000-0000-4000-8000-000000000003',task_id:'wrong-task',run_id:'run-1',
    display_id:3,captured_at_ms:finished-20,source:'bk_task_display',capture_timing:'terminal_before_release',late_capture:false};
  browseRow.result.execution_result.evidence=evidence;browseRow.updated_at=now()+2;
  await refresh();assert.ok(await evaluate(textIncludes('完整浏览验收未通过')));
  evidence.task_id=browseId;evidence.display_id=-1;browseRow.updated_at=now()+3;
  await refresh();assert.ok(await evaluate(textIncludes('完整浏览验收未通过')));
  evidence.display_id=3;evidence.run_id='old-run';browseRow.updated_at=now()+4;
  await refresh();assert.ok(await evaluate(textIncludes('完整浏览验收未通过')));
  assert.equal(await evaluate(textIncludes('结束截图已由手机采集')),false);
  evidence.run_id='run-1';browseRow.updated_at=now()+5;
  await refresh();await waitFor(textIncludes('结束截图已由手机采集'));
  passed('result includes real progress fields and rejects missing, wrong-task or wrong-run end evidence');

  state.evidence=[{...evidence,phase:'result',size:png.length,capture_status:'captured',sha256:'synthetic-image',note:'合成结束截图'},
    {...evidence,evidence_id:'00000000-0000-4000-8000-000000000004',task_id:'other-task',note:'其他任务不得混入'}];
  await navigate('/evidence/phonea?task='+browseId);
  await waitFor(textIncludes('任务结束截图'));
  assert.equal(await evaluate('document.querySelectorAll("article").length'),1);
  assert.ok(await evaluate(textIncludes('手机在任务结束、释放执行显示前采集')));
  assert.equal(await evaluate(textIncludes('其他任务不得混入')),false);
  await evaluate('document.querySelector("article button").click()');
  await waitFor('document.querySelector("[role=dialog]")!==null');
  await clickText('关闭大图');
  passed('evidence page filters by original task and opens the archived image');

  const failedId='00000000-0000-4000-8000-000000000005';
  const modelFailure='模型调用异常:model_usage_unreported: cannot enforce execution token budget';
  const failed={domain:'phonea',task_id:failedId,request_id:failedId,goal:'合成模型用量缺失任务',status:'failed',
    created_at:now(),updated_at:now(),execution:browseRow.execution,
    result:{run_id:'failed-run',reason:modelFailure,execution_result:{duration_reached:false,business_success:false,
      execution_finished:true,elapsed_ms:11805,active_ms:0,observations:0,reason:'model_usage_unreported',
      evidence:{state:'missing',reason:'actual_task_display_unavailable',display_id:-1}}}};
  state.tasks=[failed,...state.tasks];
  await navigate('/ai/phonea');await waitFor(textIncludes('模型未报告本次用量，无法核对预算。'));
  const failedArticle='[...document.querySelectorAll("article")].find(x=>x.textContent.includes("合成模型用量缺失任务"))';
  assert.ok(await evaluate(failedArticle+'.innerText.includes("本次未取得可用于结束证据的实际执行画面。")'));
  assert.ok(await evaluate(failedArticle+'.innerText.includes("未完成")&&'+failedArticle+'.innerText.includes("完整浏览验收未通过")'));
  assert.equal(await evaluate(failedArticle+'.innerText.includes("model_usage_unreported")'),false);
  assert.equal(await evaluate(failedArticle+'.innerText.includes("actual_task_display_unavailable")'),false);
  assert.equal(await evaluate(failedArticle+'.innerText.includes("登录失效")'),false);
  await evaluate('[...'+failedArticle+'.querySelectorAll("summary")].find(x=>x.textContent==="诊断详情（原始记录）").click()');
  assert.ok(await evaluate(failedArticle+'.innerText.includes('+JSON.stringify(modelFailure)+')'));
  assert.ok(await evaluate(failedArticle+'.innerText.includes("actual_task_display_unavailable")'));
  await evaluate('[...'+failedArticle+'.querySelectorAll("summary")].find(x=>x.textContent==="诊断详情（原始记录）").click()');
  failed.result.reason='budget_exhausted';failed.result.execution_result.reason='budget_exhausted';failed.updated_at=now()+1;
  await refresh();await waitFor(textIncludes('模型用量已达到本次预算，任务无法继续执行。'));
  assert.equal(await evaluate(failedArticle+'.querySelector(".border-l-2").textContent'),'模型用量已达到本次预算，任务无法继续执行。');
  assert.equal(await evaluate(failedArticle+'.innerText.includes("budget_exhausted")'),false);
  await evaluate('[...'+failedArticle+'.querySelectorAll("summary")].find(x=>x.textContent==="诊断详情（原始记录）").click()');
  assert.ok(await evaluate(failedArticle+'.innerText.includes("budget_exhausted")'));
  failed.result.reason='future_execution_reason';failed.updated_at=now()+2;
  await refresh();await waitFor(failedArticle+'.querySelector(".border-l-2").textContent==="future_execution_reason"');
  passed('known model, budget and screenshot failures have Chinese explanations; diagnostics and unknown reasons remain intact');

  await navigate('/ai/phonea');await waitFor(textIncludes('合成追问任务'));
  state.holdNextPost=true;
  await fill('#task-goal','离开设备前下达的普通任务');await clickText('交给这台手机');
  const deadline=Date.now()+3000;while(!state.delayedPost&&Date.now()<deadline)await pause(20);
  assert.ok(state.delayedPost);
  await evaluate('window.location.hash="/ai/phoneb"');
  await waitFor(textIncludes('还没有任务'));
  state.delayedPost();state.delayedPost=null;
  await pause(150);
  assert.equal(await evaluate('sessionStorage.getItem("bk-task-pending:phoneb")'),null);
  assert.equal(await evaluate(textIncludes('离开设备前下达的普通任务')),false);
  assert.ok(await evaluate('sessionStorage.getItem("bk-task-pending:phonea")!==null'));
  passed('late response after device switch cannot contaminate the new device');

  assert.equal(state.posts,2);
  assert.ok(state.calls.some(c=>c.refresh==='1'));
  if(artifacts){
    await mkdir(artifacts,{recursive:true});
    await navigate('/ai/phonea');await waitFor(textIncludes('离开设备前下达的普通任务'));
    const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
    await writeFile(join(artifacts,'ai-tasks-synthetic.png'),Buffer.from(shot.data,'base64'));
    await writeFile(join(artifacts,'browser-results.json'),JSON.stringify({passed:passes,failed:0,
      synthetic:true,productionPhoneCalls:0,modelCalls:0,businessPosts:state.posts,cancelPosts:state.cancels,answerPosts:state.answers},null,2));
  }
  console.log(JSON.stringify({passed:passes.length,failed:0,synthetic:true,productionPhoneCalls:0,modelCalls:0}));
} finally {
  if(state.delayedCancel)state.delayedCancel();
  if(state.delayedPost)state.delayedPost();
  if(socket?.readyState===1){try{await call('Browser.close',{},null);}catch{}socket.close();}
  if(browser){try{browser.kill();}catch{}}
  server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:100}).catch(()=>{});
}
