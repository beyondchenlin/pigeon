import test from 'node:test';
import assert from 'node:assert/strict';
import {VideoPolicy,videoPreference,captureScale,compatibleProfile} from '../src/lib/video-policy.ts';
test('safe auto default, manual profile and aspect-preserving short edge',()=>{
  assert.deepEqual(videoPreference(null),{resolution:'auto',fps:10});
  assert.deepEqual(videoPreference({resolution:999,fps:999}),{resolution:'auto',fps:10});
  assert.deepEqual(new VideoPolicy().profile(videoPreference({resolution:240,fps:5})),{short:240,fps:10});
  assert.equal(captureScale(360,1080,2388),1/3);assert.equal(captureScale(360,2388,1080),1/3);
  assert.equal(captureScale(720,320,480),1);
});
test('unsupported profiles fall down without being selected again, with a bounded floor',()=>{
  const rejected=new Set([480]);
  assert.deepEqual(compatibleProfile({short:480,fps:10},rejected),{short:360,fps:10});
  assert.deepEqual(compatibleProfile({short:720,fps:15},rejected),{short:720,fps:15});
  rejected.add(360);assert.deepEqual(compatibleProfile({short:480,fps:10},rejected),{short:240,fps:10});
  rejected.add(240);assert.equal(compatibleProfile({short:480,fps:10},rejected),null);
});
test('congestion lowers quickly, recovery requires sustained good samples without oscillation',()=>{
  const p=new VideoPolicy(),bad={rttMs:400,loss:0,bufferMs:0,decodeMs:0},good={...bad,rttMs:60};
  assert.equal(p.sample(bad,0),false);assert.equal(p.sample(bad,2000),true);assert.equal(p.level,0);
  for(let i=0;i<14;i++)assert.equal(p.sample(good,4000+i*2000),false);
  assert.equal(p.sample(good,32000),true);assert.equal(p.level,1);
  assert.equal(p.sample(bad,33000),false);assert.equal(p.sample(bad,34000),false);
  assert.equal(p.level,1);assert.equal(p.sample(bad,42000),true);
});
test('fast auto keeps working 360 video on a good network; manual HD remains available',()=>{
 const p=new VideoPolicy(),good={rttMs:45,loss:0,bufferMs:10,decodeMs:1};
 for(let i=0;i<120;i++)assert.equal(p.sample(good,i*2000),false);
 assert.deepEqual(p.profile(videoPreference(null)),{short:360,fps:10});
 assert.deepEqual(p.profile({resolution:480,fps:15}),{short:480,fps:15});
 assert.deepEqual(p.profile({resolution:720,fps:20}),{short:720,fps:20});
});
