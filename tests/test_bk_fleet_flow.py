"""Integrated authenticated plan -> durable job -> simulated phone receipt."""
from contextlib import contextmanager
import asyncio
import json
import os
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application
from executor.models import Device, DeviceJob, Group, Job, ModelResource, Task, User, UserDevice
from server import bk_ai, bk_fleet
from server.bk_ai_store import AiTask
from server.bk_native_reload import NativeReload
from server.bk_fleet_store import FleetDraft
from server.service import PlatformJobHandler


class FleetFlowTests(AsyncHTTPTestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        for table in (User.__table__, Device.__table__, Group.__table__, UserDevice.__table__,
                      ModelResource.__table__, Job.__table__, DeviceJob.__table__, Task.__table__, AiTask.__table__, FleetDraft.__table__, NativeReload.__table__):
            table.create(self.engine)
        factory = sessionmaker(self.engine, expire_on_commit=False)
        @contextmanager
        def sessions():
            with factory() as session, session.begin():
                yield session
        self.sessions = sessions
        with sessions() as s:
            s.add(User(id=1, name='owner'))
            s.add(Group(id=1, owner_id=1, name='一组'))
            s.add(Device(id=1, domain='phonea', comment='手机一', state='online'))
            s.add(UserDevice(user_id=1, device_id=1, group_id=1))
            s.add(ModelResource(id=1, owner_id=1, name='model', provider='openai_compatible',
                                api_base='https://model.example/v1', api_key='test-only',
                                model_name='example', temperature=0.5))
        self.calls = []
        def phone(domain, method, path, body=None):
            self.calls.append((domain, method, path))
            if path == '/api/v1/capabilities':
                return {'http':200, 'body':{'execution_versions':[1], 'task_wait_revision':True, 'terminal_evidence':True}}
            reply = {'task_id': body['request_id'], 'status': 'done', 'answer': '12'}
            if 'execution' in body:
                reply.update(execution=body['execution'], revision=1, sampled_at_ms=1800000000000,
                             execution_result={'execution_finished':True, 'finished_at_ms':1800000000000,
                                               'duration_reached':True, 'business_success':True})
            return {'http': 201, 'body': reply}
        candidate = {'goal': '只回答12', 'target': {'kind': 'group', 'name': '一组'}, 'schedule': {'kind': 'now'}}
        self.patches = [patch.object(bk_ai, 'session_scope', sessions), patch.object(bk_fleet, 'session_scope', sessions),
                        patch.object(bk_ai, 'mapping', return_value={'info':'/simulated/legacy-info.json'}),
                        patch.object(bk_ai, 'call', side_effect=phone), patch.object(bk_fleet, 'interpret', return_value=candidate)]
        for item in self.patches: item.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for item in reversed(self.patches): item.stop()
        self.engine.dispose()
        bk_ai.INFLIGHT.clear()

    def get_app(self):
        def auth(handler, session=None):
            return SimpleNamespace(id=int(handler.request.headers.get('X-Test-User', '1')))
        class Chat(bk_fleet.FleetChatPlanHandler): get_login_user = auth
        class Jobs(bk_fleet.FleetJobHandler): get_login_user = auth
        class Draft(bk_fleet.FleetDraftHandler): get_login_user = auth
        return Application([(r'/chat', Chat), (r'/draft/([a-f0-9-]{36})', Draft),
                            (r'/jobs(?:/([0-9]+)(?:/(cancel))?)?', Jobs)], ctl=None)

    def post_json(self, path, value):
        response = self.fetch(path, method='POST', body=json.dumps(value))
        return response.code, json.loads(response.body)

    def create_job(self):
        code, preview = self.post_json('/chat', {'message':'让一组只回答12', 'model_id':1, 'timezone':'Asia/Shanghai'})
        self.assertEqual(code, 200)
        draft_id = preview['data']['draft_id']
        saved = self.fetch('/draft/' + draft_id)
        self.assertEqual(json.loads(saved.body)['data']['state'], 'ready')
        code, created = self.post_json('/jobs', {'request_id':draft_id, 'plan':preview['data']['plan']})
        self.assertEqual(code, 202)
        saved = self.fetch('/draft/' + draft_id)
        self.assertEqual(json.loads(saved.body)['data']['state'], 'submitted')
        return created['data']['id']

    def test_chat_to_completed_phone_receipt(self):
        job_id = self.create_job()
        bk_fleet._poll_once()
        asyncio.run(bk_ai.poll())
        bk_fleet._poll_once()
        self.assertEqual(self.calls, [('phonea', 'POST', '/api/v1/tasks')])
        with self.sessions() as s:
            self.assertEqual(s.query(AiTask).one().status, 'done')
            self.assertEqual(s.query(Task).one().state, 'success')
            self.assertEqual(s.get(Job, job_id).success, 1)
            serialized = PlatformJobHandler.to_dict(None, s.get(Job, job_id))
            self.assertEqual(serialized['mode'], 'bk_managed')
        result = self.fetch('/jobs/' + str(job_id))
        self.assertEqual(json.loads(result.body)['data']['devices'][0]['last_task']['state'], 'success')

    def test_stop_before_dispatch_never_sends_to_phone(self):
        job_id = self.create_job()
        bk_fleet._poll_once()
        code, _ = self.post_json('/jobs/' + str(job_id) + '/cancel', {'stop_current':True})
        self.assertEqual(code, 200)
        asyncio.run(bk_ai.poll())
        bk_fleet._poll_once()
        self.assertEqual(self.calls, [])
        with self.sessions() as s:
            self.assertEqual(s.query(AiTask).one().status, 'cancelled')
            self.assertEqual(s.query(Task).one().state, 'revoked')

    def test_lost_submit_response_only_queries_same_task(self):
        self.create_job()
        bk_fleet._poll_once()
        calls = []
        def phone(domain, method, path, body=None):
            calls.append((method, path))
            if method == 'POST':
                raise bk_ai.TransportError('connection_unknown')
            return {'http':200, 'body': {'task_id':path.rsplit('/', 1)[-1], 'status':'done'}}
        with patch.object(bk_ai, 'call', side_effect=phone):
            asyncio.run(bk_ai.poll())
            asyncio.run(bk_ai.poll())
        self.assertEqual([method for method, _ in calls], ['POST', 'GET'])

    def test_browse_contract_is_dispatched_with_capability_check(self):
        execution = {'version':1, 'kind':'browse', 'duration_s':7200, 'clock':'elapsed',
            'finish_timeout_s':60, 'stop_timeout_s':30, 'progress_interval_s':600, 'event_latency_s':30,
            'budget':{'max_tokens':50000}, 'progress_requirements':{'min_observations':2,
                'min_active_s':60, 'max_stall_s':600}}
        code, preview = self.post_json('/chat', {'message':'让一组浏览', 'model_id':1,
                                                 'timezone':'Asia/Shanghai', 'execution':execution})
        self.assertEqual(code, 200)
        self.assertEqual(preview['data']['plan']['execution'], execution)
        code, created = self.post_json('/jobs', {'request_id':preview['data']['draft_id'], 'plan':preview['data']['plan']})
        self.assertEqual(code, 202)
        bk_fleet._poll_once()
        asyncio.run(bk_ai.poll())
        self.assertEqual([entry[2] for entry in self.calls], ['/api/v1/capabilities', '/api/v1/tasks'])
        with self.sessions() as s:
            row = s.query(AiTask).one()
            self.assertEqual(row.execution, execution)
            self.assertEqual(row.status, 'done')

    def test_clarification_is_durable_and_reuses_same_draft(self):
        draft_id = str(uuid.uuid4())
        candidate = {'goal':'只回答12', 'target':{'kind':'group', 'name':'一组'}, 'schedule':{'kind':'now'}}
        with patch.object(bk_fleet, 'interpret', side_effect=[{'clarification':'请选择分组'}, candidate]):
            code, first = self.post_json('/chat', {'draft_id':draft_id, 'message':'回答12', 'model_id':1, 'timezone':'Asia/Shanghai'})
            self.assertEqual(code, 200)
            self.assertEqual(first['data']['clarification'], '请选择分组')
            saved = json.loads(self.fetch('/draft/' + draft_id).body)['data']
            self.assertEqual((saved['state'], saved['question']), ('clarify', '请选择分组'))
            code, second = self.post_json('/chat', {'draft_id':draft_id, 'message':'一组回答12', 'model_id':1, 'timezone':'Asia/Shanghai'})
        self.assertEqual(code, 200)
        self.assertEqual(second['data']['draft_id'], draft_id)
        with self.sessions() as s:
            self.assertEqual(s.query(FleetDraft).count(), 1)
            self.assertEqual(s.get(FleetDraft, draft_id).state, 'ready')

    def test_model_cannot_choose_a_target_user_never_named(self):
        code, response = self.post_json('/chat', {'message':'回答12', 'model_id':1, 'timezone':'Asia/Shanghai'})
        self.assertEqual(code, 200)
        self.assertIn('clarification', response['data'])
        with self.sessions() as s:
            self.assertEqual(s.query(Job).count(), 0)

    def test_other_user_cannot_view_or_cancel_job(self):
        job_id = self.create_job()
        self.assertEqual(self.fetch('/jobs/' + str(job_id), headers={'X-Test-User':'2'}).code, 404)
        response = self.fetch('/jobs/' + str(job_id) + '/cancel', method='POST',
                              headers={'X-Test-User':'2'}, body=json.dumps({'stop_current':True}))
        self.assertEqual(response.code, 404)
        with self.sessions() as s:
            self.assertNotEqual(s.get(Job, job_id).state, 'bk_stopped')


if __name__ == '__main__': unittest.main()
