import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareTask, dispatchTask, terminalTask, taskProgress, validateExecution, supervisionSeconds, stoppingTask, mergeTaskReceipt, mergeTaskReceipts } from '../src/lib/task-request.mjs';

test('natural goal stays unchanged and technical parameters are generated internally', async () => {
  const request = await prepareTask('phonea', '搜索二糖，进入会话，静默5分钟后截图归档', '00000000-0000-4000-8000-000000000001');
  assert.deepEqual(Object.keys(request.body), ['goal','request_id','timeout_s']);
  assert.equal(request.taskId, '1888f19c-70aa-5ac5-8c67-cf451cb4d5f6'); // Python uuid.uuid5 reference
  assert.equal(request.body.goal, '搜索二糖，进入会话，静默5分钟后截图归档');
  assert.equal(request.taskId, (await prepareTask('phonea', 'different goal', request.requestId)).taskId);
  assert.notEqual(request.taskId, (await prepareTask('phoneb', request.body.goal, request.requestId)).taskId);
});
test('write-ahead receipt prevents an untrackable submission and lost response never repeats POST', async () => {
  const request = await prepareTask('phonea', 'Read only');
  let posts = 0; let saved;
  await assert.rejects(dispatchTask(request, () => { throw new Error('storage full'); }, () => { posts++; }));
  assert.equal(posts, 0);
  await assert.rejects(dispatchTask(request, p => { saved = p; }, () => { posts++; throw new Error('response lost'); }));
  assert.deepEqual(saved, {taskId:request.taskId, requestId:request.requestId});
  assert.equal(posts, 1);
  assert.equal(terminalTask('unknown'), false);
});

test('explicit timeout keeps third requestId compatible and defaults to 900', async () => {
  const id = '00000000-0000-4000-8000-000000000001';
  assert.equal((await prepareTask('phonea', 'read', id)).body.timeout_s, 900);
  for (const timeout_s of [15, 900, 7200, 10800]) {
    const request = await prepareTask('phonea', 'read', id, {timeout_s});
    assert.equal(request.requestId, id);
    assert.equal(request.taskId, '1888f19c-70aa-5ac5-8c67-cf451cb4d5f6');
    assert.equal(request.body.timeout_s, timeout_s);
  }
  for (const timeout_s of [14, 10801, 900.5, '900', null, NaN, Infinity])
    await assert.rejects(prepareTask('phonea', 'read', id, {timeout_s}), /15.*10800/);
});
test('long-task timeout and goal are never included in persisted IDs', async () => {
  const request = await prepareTask('phonea', 'private goal', undefined, {timeout_s:10800});
  let saved, posts = 0;
  await assert.rejects(dispatchTask(request, p => {saved=p;}, () => {posts++;throw Error('lost');}));
  assert.deepEqual(saved, {taskId:request.taskId, requestId:request.requestId});
  assert.equal(posts, 1);
});
test('progress preserves step zero and marks unknown or disconnected snapshots stale', () => {
  const task = {status:'running',result:{progress:'已完成第一步',step:0}};
  assert.deepEqual(taskProgress(task), {progress:'已完成第一步',step:'0',stale:false});
  assert.equal(taskProgress({...task,status:'unknown'}).stale,true);
  assert.equal(taskProgress(task,true).stale,true);
  assert.deepEqual(taskProgress({status:'running'}), {progress:'',step:'',stale:false});
});

const browse = () => ({ version:1, kind:'browse', duration_s:7200, clock:'elapsed', finish_timeout_s:60,
  stop_timeout_s:30, progress_interval_s:600, event_latency_s:30, budget:{max_tokens:100000},
  progress_requirements:{min_observations:20,min_active_s:6000,max_stall_s:120} });

test('browse parameters are independent of the goal and supervision covers startup, business and finish', async () => {
  const execution = browse();
  const request = await prepareTask('phonea','只浏览',undefined,{execution});
  assert.equal(request.body.timeout_s, 7440);
  assert.deepEqual(request.body.execution, execution);
  execution.budget.max_tokens = 1;
  assert.equal(request.body.execution.budget.max_tokens, 100000);
  await assert.rejects(prepareTask('phonea','只浏览',undefined,{execution:browse(),timeout_s:900}), /监督/);
  assert.equal(supervisionSeconds(browse()),7440);
});

test('same identity preserves all changed execution parameters for server conflict detection', async () => {
  const id='00000000-0000-4000-8000-000000000001';
  const original = await prepareTask('phonea','只浏览',id,{execution:browse()});
  for (const edit of [e=>e.duration_s=7100,e=>e.finish_timeout_s=90,e=>e.stop_timeout_s=40,
    e=>e.progress_interval_s=500,e=>e.event_latency_s=20,e=>e.budget.max_tokens=50000,
    e=>e.progress_requirements.min_observations=30,e=>e.progress_requirements.min_active_s=5000,
    e=>e.progress_requirements.max_stall_s=100]) {
    const execution=browse(); edit(execution);
    const changed=await prepareTask('phonea','只浏览',id,{execution});
    assert.equal(changed.taskId,original.taskId);
    assert.notDeepEqual(changed.body,original.body);
  }
});

test('unsupported, missing, malformed and semantically invalid contracts fail before submission', async () => {
  for(const edit of [e=>delete e.clock,e=>e.extra=true,e=>e.version=2,e=>e.clock='active',
    e=>e.kind='publish',e=>e.duration_s=10001,e=>e.duration_s=1.5,e=>e.duration_s='7200',
    e=>e.budget={},e=>e.budget.max_tokens=0,e=>e.budget.max_tokens=10000001,e=>e.budget.max_cost=10,
    e=>e.progress_requirements.min_observations=1000001,
    e=>e.progress_requirements.min_observations=1,e=>e.progress_requirements.min_active_s=7201,
    e=>e.progress_requirements.max_stall_s=0,e=>e.event_latency_s=31,e=>e.stop_timeout_s=121]) {
    const execution=browse(); edit(execution); assert.throws(()=>validateExecution(execution));
    await assert.rejects(prepareTask('phonea','只浏览',undefined,{execution}));
  }
  for (const execution of [null,undefined,[],true]) await assert.rejects(prepareTask('phonea','只浏览',undefined,{execution}));
  await assert.rejects(prepareTask('phonea','只浏览',undefined,{budget:1}));
});

test('a lost browser submission persists only the original identity and never retries business creation', async () => {
  const request=await prepareTask('phonea','私人目标',undefined,{execution:browse()});
  let saved;let posts=0;
  await assert.rejects(dispatchTask(request,p=>{saved=p;},()=>{posts++;throw Error('lost');}));
  assert.deepEqual(saved,{taskId:request.taskId,requestId:request.requestId});
  assert.equal(posts,1);
});

test('stop intent and overdue states remain occupied until a real terminal receipt arrives', () => {
  for(const status of ['stopping','stop_unconfirmed','finishing','finish_overdue','unknown']) assert.equal(terminalTask(status),false);
  const running={task_id:'one',status:'running',cancel_requested:true,updated_at:2};
  const late={task_id:'one',status:'running',cancel_requested:false,updated_at:3};
  assert.equal(stoppingTask(mergeTaskReceipt(running,late)),true);
  assert.equal(stoppingTask({...running,status:'cancelled'}),false);
  assert.equal(terminalTask('cancelled'),true);
});

test('old or missing list responses cannot regress terminal state or free an unresolved active task', () => {
  const done={task_id:'one',status:'done',updated_at:20,result:{answer:'verified'}};
  const old={task_id:'one',status:'running',updated_at:30};
  assert.deepEqual(mergeTaskReceipt(done,old),done);
  assert.deepEqual(mergeTaskReceipt(done,{...done,updated_at:10,result:{answer:'stale'}}),done);
  const pending={task_id:'two',status:'stop_unconfirmed',updated_at:15};
  assert.deepEqual(mergeTaskReceipts([done,pending],[old]),[done,pending]);
});
