import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareTask, dispatchTask, terminalTask } from '../src/lib/task-request.mjs';

test('natural goal stays unchanged and technical parameters are generated internally', async () => {
  const request = await prepareTask('phonea', '搜索二糖，进入会话，静默5分钟后截图归档', '00000000-0000-4000-8000-000000000001');
  assert.deepEqual(Object.keys(request.body), ['goal','request_id']);
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
