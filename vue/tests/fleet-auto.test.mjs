import { strict as assert } from 'node:assert';
import { canAutoArrange } from '../src/lib/fleet-auto.mjs';

const plan = {target:{name:'一组'}, goal:'只回答12', schedule:{kind:'now'}};
assert.equal(canAutoArrange('让一组只回答12', plan), true);
assert.equal(canAutoArrange('请让一组只回答12', plan), true);
assert.equal(canAutoArrange('让一组只回答12，然后发布', plan), false);
assert.equal(canAutoArrange('让一组不要发布', {...plan, goal:'发布'}), false);
assert.equal(canAutoArrange('让一组每天只回答12', {...plan, goal:'每天只回答12'}), false);
assert.equal(canAutoArrange('让一组稍后只回答12', {...plan, goal:'稍后只回答12'}), false);
assert.equal(canAutoArrange('让一组只回答12', {...plan, schedule:{kind:'daily'}}), false);
assert.equal(canAutoArrange('让一组只回答12', {...plan, execution:{duration_s:7200}}), false);
