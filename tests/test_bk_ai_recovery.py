"""Recovery invariants using isolated SQLite receipts and a simulated phone only."""
import asyncio
from contextlib import contextmanager
import importlib.util
import os
import time
import unittest
from unittest.mock import patch
if importlib.util.find_spec('sqlalchemy') is None or 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in existing PIGEON runtime')
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from executor.models import Device, UserDevice
from server import bk_ai as api
from server.bk_ai_store import AiTask

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread':False})
        for table in (Device.__table__, UserDevice.__table__, AiTask.__table__): table.create(self.engine)
        factory = sessionmaker(self.engine, expire_on_commit=False)
        @contextmanager
        def sessions():
            with factory() as session, session.begin(): yield session
        self.sessions = sessions
        self.patcher = patch.object(api, 'session_scope', sessions)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.engine.dispose)
        api.INFLIGHT.clear()
        self.addCleanup(api.INFLIGHT.clear)
    def receipt(self, n=1, status='running', deadline=None):
        task_id = '00000000-0000-4000-8000-%012d' % n
        with self.sessions() as s:
            s.add(Device(id=n,domain='phone%d'%n,state='online'))
            s.add(AiTask(id=task_id,device_id=n,user_id=1,request_id=task_id,goal='read only',status=status,
                snapshot={'task_id':task_id,'status':'running','step':9,'progress':'last confirmed step'},history=[],
                created=float(n),updated=float(n),deadline=deadline or time.time()+10800,cancel_requested=False))
        return task_id
    def test_disconnect_preserves_progress_and_reconnect_queries_same_id(self):
        task_id=self.receipt()
        with patch.object(api,'call',side_effect=api.TransportError('connection_unknown')) as call:
            api.reconcile(task_id)
            self.assertEqual(call.call_args.args[1:3],('GET','/api/v1/tasks/'+task_id))
        with self.sessions() as s:
            row=s.get(AiTask,task_id)
            self.assertEqual(row.status,'unknown')
            self.assertEqual(row.snapshot['step'],9)
        with patch.object(api,'call',return_value={'http':200,'body':{'task_id':task_id,'status':'done','answer':'4'}}) as call:
            api.reconcile(task_id)
            self.assertEqual(call.call_args.args[1],'GET')
        with self.sessions() as s:self.assertEqual(s.get(AiTask,task_id).status,'done')
    def test_phone_interrupted_is_terminal_not_automatic_restart(self):
        task_id=self.receipt()
        with patch.object(api,'call',return_value={'http':200,'body':{'task_id':task_id,'status':'failed','reason':'interrupted'}}) as call:
            api.reconcile(task_id);api.reconcile(task_id)
            self.assertEqual(call.call_count,1)
            self.assertEqual(call.call_args.args[1],'GET')
        with self.sessions() as s:self.assertEqual(s.get(AiTask,task_id).status,'failed')
    def test_expired_offline_task_keeps_cancel_intent_until_reconnected(self):
        task_id=self.receipt(status='queued',deadline=1)
        with patch.object(api,'call',side_effect=api.TransportError('connection_unknown')):api.reconcile(task_id)
        with self.sessions() as s:self.assertTrue(s.get(AiTask,task_id).cancel_requested)
        replies=[{'http':200,'body':{'task_id':task_id,'status':'queued'}},
                 {'http':202,'body':{'accepted':True}},
                 {'http':200,'body':{'task_id':task_id,'status':'cancelled'}}]
        with patch.object(api,'call',side_effect=replies) as call:
            api.reconcile(task_id)
            self.assertEqual([(c.args[1],c.args[2]) for c in call.call_args_list],
                [('GET','/api/v1/tasks/'+task_id),('POST','/api/v1/tasks/'+task_id+'/cancel'),('GET','/api/v1/tasks/'+task_id)])
        with self.sessions() as s:self.assertEqual(s.get(AiTask,task_id).status,'cancelled')
    def test_poll_rotates_all_devices_instead_of_starving_after_four(self):
        ids=[self.receipt(n) for n in range(1,7)]
        seen=[]
        async def perform(task_id, submit=False):
            seen.append(task_id)
            with self.sessions() as s:s.get(AiTask,task_id).updated=time.time()
        with patch.object(api,'perform',perform):
            asyncio.run(api.poll());asyncio.run(api.poll())
        self.assertEqual(set(seen),set(ids))
        self.assertEqual(seen[:4],ids[:4])

    def test_restart_dispatches_only_receipts_without_dispatch_marker(self):
        unsent, uncertain = self.receipt(1, status='submitting'), self.receipt(2, status='unknown')
        with self.sessions() as s:
            s.get(AiTask, unsent).sync_meta = {'admitted': True}
            s.get(AiTask, uncertain).sync_meta = {'admitted': True, 'dispatch_started_at': 1}
        seen = []
        async def perform(task_id, submit=False):
            seen.append((task_id, submit))
        with patch.object(api, 'perform', perform):
            asyncio.run(api.poll())
        self.assertEqual(dict(seen), {unsent: True, uncertain: False})

    def test_scheduled_task_is_not_sent_after_permission_revocation(self):
        task_id = self.receipt(1, status='submitting')
        with self.sessions() as s:
            s.get(AiTask, task_id).sync_meta = {'admitted': True, 'job_id': 9}
        with patch.object(api, 'call') as call:
            api.reconcile(task_id, submit=True)
            call.assert_not_called()
        with self.sessions() as s:
            self.assertEqual(s.get(AiTask, task_id).error, 'access_revoked_before_dispatch')

    def test_scheduled_task_is_not_sent_when_device_goes_offline(self):
        task_id = self.receipt(1, status='submitting')
        with self.sessions() as s:
            s.add(UserDevice(user_id=1, device_id=1))
            s.get(Device, 1).state = 'offline'
            s.get(AiTask, task_id).sync_meta = {'admitted': True, 'job_id': 9}
        with patch.object(api, 'call') as call:
            api.reconcile(task_id, submit=True)
            call.assert_not_called()
        with self.sessions() as s:
            self.assertEqual(s.get(AiTask, task_id).error, 'device_offline_before_dispatch')

if __name__=='__main__':unittest.main()
