"""Run in the existing PIGEON runtime; SQLite receipts and a fake phone only."""
from contextlib import contextmanager
import json
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch
import importlib.util
import os
if importlib.util.find_spec('sqlalchemy') is None or 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run API integration tests in the existing PIGEON runtime')
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application
from tornado.platform.asyncio import BaseAsyncIOLoop
from executor.models import Device, Task
from server import bk_ai as api
from server.bk_ai_store import AiTask, AiAnswer, active, script_busy


class PollStartupTests(unittest.TestCase):
    def test_poll_runs_on_the_loop_created_inside_server_start(self):
        import asyncio
        import uvloop
        from tornado.ioloop import IOLoop
        http = SimpleNamespace()
        seen = []
        previous = BaseAsyncIOLoop.start
        async def observe():
            seen.append(asyncio.get_running_loop())
            http.ioloop.stop()
        def original_start(manager):
            asyncio.set_event_loop(uvloop.new_event_loop())
            manager.ioloop = IOLoop.current()
            manager.ioloop.call_later(3, manager.ioloop.stop)
            manager.ioloop.start()
        try:
            with patch.object(api, 'poll', observe):
                api.start_with_polling(http, original_start)
            self.assertEqual(seen, [http.ioloop.asyncio_loop])
            self.assertIs(BaseAsyncIOLoop.start, previous)
        finally:
            if hasattr(http, '_bk_ai_poll'):
                http._bk_ai_poll.stop()
            if hasattr(http, 'ioloop'):
                http.ioloop.close(all_fds=True)
            asyncio.set_event_loop(None)


class AiApiTests(AsyncHTTPTestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        for table in (Device.__table__, Task.__table__, AiTask.__table__, AiAnswer.__table__):
            table.create(self.engine)
        factory = sessionmaker(self.engine, expire_on_commit=False)
        @contextmanager
        def session_scope():
            with factory() as session, session.begin():
                yield session
        self.sessions = session_scope
        with self.sessions() as s:
            s.add_all([Device(id=1, domain='phonea', state='online'), Device(id=2, domain='phoneb', state='online')])
        self.calls = []
        self.remote = {}
        def call(domain, method, path, body=None):
            self.calls.append((domain, method, path, body))
            if method == 'POST' and path == '/api/v1/tasks':
                self.remote[body['request_id']] = dict(task_id=body['request_id'], status='running', step=1)
                return dict(http=201, body=self.remote[body['request_id']])
            task_id = path.split('/')[4]
            if path.endswith('/answer'):
                self.remote[task_id] = dict(task_id=task_id, status='running')
                return dict(http=200, body=dict(task_id=task_id, accepted=True))
            if path.endswith('/cancel'):
                self.remote[task_id] = dict(task_id=task_id, status='cancelled')
                return dict(http=202, body={'accepted': True})
            return dict(http=200, body=self.remote[task_id])
        self.patches = [patch.object(api, 'session_scope', self.sessions), patch.object(api, 'mapping', return_value={}),
                        patch.object(api, 'call', side_effect=call)]
        for p in self.patches:
            p.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in reversed(self.patches):
            p.stop()
        self.engine.dispose()
        api.INFLIGHT.clear()

    def get_app(self):
        sessions = self.sessions
        class Handler(api.AiHandler):
            def get_login_user_device(self, domain):
                if self.request.headers.get('X-Test-Auth') != 'owner':
                    self.throw(401, 'AUTH', 'unauthorized')
                with sessions() as s:
                    device = s.query(Device).filter_by(domain=domain).one()
                    return SimpleNamespace(id=int(self.request.headers.get('X-Test-User', '1'))), device
        return Application([(r'/api/v1/device/([a-z0-9]+)/ai/tasks(?:/([a-f0-9-]{36})(?:/(cancel|takeover|answer))?)?', Handler)], ctl=None)

    def request(self, body=None, suffix='', domain='phonea', headers=None):
        reply = self.fetch('/api/v1/device/' + domain + '/ai/tasks' + suffix,
            method='POST' if body is not None else 'GET', body=None if body is None else json.dumps(body),
            headers={'X-Test-Auth': 'owner', **(headers or {})})
        return reply.code, json.loads(reply.body)

    def submit(self, **extra):
        body = dict(goal='Read system information only', request_id=str(uuid.uuid4()), timeout_s=60, **extra)
        return body, self.request(body)

    def test_create_duplicate_conflict_and_result(self):
        body, (code, reply) = self.submit()
        self.assertEqual(code, 202)
        task_id = reply['data']['task_id']
        self.assertEqual(reply['data']['status'], 'running')
        self.assertEqual(self.request(body)[1]['data']['task_id'], task_id)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.request(dict(body, goal='different'))[0], 409)
        self.remote[task_id] = dict(task_id=task_id, status='done', answer='Android 14')
        result = self.request(suffix='/' + task_id)[1]['data']
        self.assertEqual(result['result']['answer'], 'Android 14')
        self.assertEqual(result['status'], 'done')

    def test_web_and_mcp_share_goal_and_default_budget(self):
        body = dict(goal='搜索二糖，进入会话，静默5分钟后截图归档', request_id=str(uuid.uuid4()))
        with patch.dict(os.environ, {'BK_AI_ORIGIN': 'https://pilot.example'}):
            headers = {'Origin': 'https://pilot.example', 'X-Requested-With': 'BK-Task'}
            code, result = self.request(body, headers=headers)
            self.assertEqual(code, 202)
            row = result['data']
            self.assertEqual(row['goal'], body['goal'])
            self.assertEqual(round(row['deadline'] - row['created_at']), 900)
            self.assertEqual(self.calls[0][3]['goal'], body['goal'])
            self.assertEqual(self.request(body)[1]['data']['task_id'], row['task_id'])
            self.assertEqual(len(self.calls), 1)

    def test_browser_uses_configured_public_origin_without_entrypoint_environment(self):
        body = dict(goal='Read only', request_id=str(uuid.uuid4()))
        with patch.dict(os.environ, {'BK_AI_ORIGIN': ''}), patch.object(api, 'PUBLIC_ORIGIN', 'https://pilot.example'):
            code, result = self.request(body, headers={'Origin': 'https://pilot.example', 'X-Requested-With': 'BK-Task'})
            self.assertEqual(code, 202)
            self.assertEqual(result['data']['goal'], body['goal'])

    def test_browser_origin_and_explicit_header_are_required(self):
        body = dict(goal='Read only', request_id=str(uuid.uuid4()))
        with patch.dict(os.environ, {'BK_AI_ORIGIN': 'https://pilot.example'}):
            for headers in ({'Origin':'https://evil.example','X-Requested-With':'BK-Task'},
                            {'Origin':'https://pilot.example'}, {'Origin':'null','X-Requested-With':'BK-Task'},
                            {'Cookie':'token=any-cookie'}):
                self.assertEqual(self.request(body, headers=headers)[0], 403)
        self.assertEqual(self.calls, [])

    def test_other_device_member_cannot_read_cancel_or_reuse_private_task(self):
        body, (_, result) = self.submit()
        task_id = result['data']['task_id']
        headers = {'X-Test-User':'2'}
        self.assertEqual(self.request(headers=headers)[1]['data']['tasks'], [])
        self.assertEqual(self.request(suffix='/' + task_id, headers=headers)[0], 404)
        self.assertEqual(self.request({}, '/' + task_id + '/cancel', headers=headers)[0], 404)
        self.assertEqual(self.request(body, headers=headers)[0], 409)
        self.assertEqual(len(self.calls), 1)

    def test_unknown_submit_and_poll_timeout_never_replay(self):
        body = dict(goal='Read only', request_id=str(uuid.uuid4()))
        with patch.object(api, 'call', side_effect=api.TransportError('connection_unknown')) as call:
            code, result = self.request(body)
            task_id = result['data']['task_id']
            self.assertEqual(result['data']['status'], 'unknown')
            self.request(body)
            self.request(suffix='/' + task_id)
            self.assertEqual([c.args[1] for c in call.call_args_list], ['POST', 'GET'])
            self.assertEqual(self.submit()[1][0], 409)
        with patch.object(api, 'call', return_value={'http': 404, 'body': {}}):
            self.assertEqual(self.request(suffix='/' + task_id)[1]['data']['status'], 'unknown')

    def test_cancel_observes_terminal(self):
        _, (_, result) = self.submit()
        task_id = result['data']['task_id']
        code, result = self.request({}, '/' + task_id + '/cancel')
        self.assertEqual(code, 200)
        self.assertEqual(result['data']['status'], 'cancelled')
        self.assertTrue(result['data']['cancel_requested'])

    def test_cancel_accepted_is_not_terminal(self):
        _, (_, result) = self.submit()
        task_id = result['data']['task_id']
        with patch.object(api, 'call', side_effect=lambda d, m, p, b=None: {'http':202,'body':{'accepted':True}} if m=='POST' else {'http':200,'body':self.remote[task_id]}):
            code, result = self.request({}, '/' + task_id + '/cancel')
        self.assertEqual(code, 202)
        self.assertEqual(result['data']['status'], 'running')

    def test_ownership_offline_and_device_mapping(self):
        self.assertEqual(self.request(headers={'X-Test-Auth':'wrong'})[0], 401)
        body, (_, result) = self.submit()
        self.assertEqual(self.request(suffix='/' + result['data']['task_id'], domain='phoneb')[0], 404)
        self.assertEqual(self.request(body, domain='phoneb')[0], 202)
        self.assertNotEqual(self.calls[0][3]['request_id'], self.calls[1][3]['request_id'])
        with self.sessions() as s:
            s.get(Device, 2).state = 'offline'
        self.assertEqual(self.request(dict(body, request_id=str(uuid.uuid4())), domain='phoneb')[0], 503)

    def test_scripts_and_manual_control_block_admission(self):
        with self.sessions() as s:
            s.add(Task(device_id=1, task_id='script', state='running'))
        self.assertEqual(self.submit()[1][0], 409)
        with self.sessions() as s:
            s.query(Task).delete()
            s.get(Device, 1).controlling = True
        self.assertEqual(self.submit()[1][0], 409)
        self.assertEqual(self.calls, [])

    def test_phone_auth_error_is_definite_refusal(self):
        with patch.object(api, 'call', return_value={'http':401, 'body':{}}):
            self.assertEqual(self.submit()[1][1]['data']['status'], 'rejected')
        with self.sessions() as s:
            self.assertIsNone(active(s, 1))

    def test_refusal_retains_safe_reason_and_duplicate_never_resubmits(self):
        reply = {'http': 409, 'body': {'error': {'code': 'device_not_ready', 'message': 'private'}}}
        with patch.object(api, 'call', return_value=reply) as call:
            body, (_, response) = self.submit()
            task = response['data']
            self.assertEqual(task['status'], 'rejected')
            self.assertEqual(task['error'], 'phone_refused_409:device_not_ready')
            self.assertNotIn('private', json.dumps(task))
            self.request(body)
            self.request(suffix='/' + task['task_id'])
            self.assertEqual(call.call_count, 1)

    def test_expired_task_requests_cancellation(self):
        _, (_, result) = self.submit()
        task_id = result['data']['task_id']
        with self.sessions() as s:
            s.get(AiTask, task_id).deadline = 1
        self.assertEqual(self.request(suffix='/' + task_id)[1]['data']['status'], 'cancelled')

    def test_restart_with_durable_unsubmitted_receipt_only_queries(self):
        _, (_, result) = self.submit()
        task_id = result['data']['task_id']
        with self.sessions() as s:
            row = s.get(AiTask, task_id)
            row.status, row.snapshot = 'submitting', {}
        with patch.object(api, 'call', return_value={'http':404,'body':{}}) as call:
            self.assertEqual(self.request(suffix='/' + task_id)[1]['data']['status'], 'unknown')
            self.assertEqual([c.args[1] for c in call.call_args_list], ['GET'])

    def test_malformed_phone_reply_remains_unresolved(self):
        with patch.object(api, 'call', return_value={'http':200,'body':[]}):
            self.assertEqual(self.submit()[1][1]['data']['status'], 'unknown')

    def question(self):
        _, (_, result) = self.submit()
        task_id = result['data']['task_id']
        self.remote[task_id] = dict(task_id=task_id, status='waiting_for_answer', answerable=True,
                                    question='Continue reading?', options=['yes', 'no'])
        result = self.request(suffix='/' + task_id)[1]['data']
        return task_id, dict(request_id=str(uuid.uuid4()), question_key=result['question_key'], text='yes')

    def test_answer_continues_same_task_once_and_cannot_reuse_id(self):
        task_id, body = self.question()
        self.assertEqual(self.request(body, '/' + task_id + '/answer')[1]['data']['status'], 'running')
        self.assertEqual(self.request(body, '/' + task_id + '/answer')[0], 200)
        self.assertEqual(self.request(dict(body, text='no'), '/' + task_id + '/answer')[0], 409)
        self.assertEqual(len([c for c in self.calls if c[2].endswith('/answer')]), 1)
        self.assertEqual(len([c for c in self.calls if c[1:3] == ('POST','/api/v1/tasks')]), 1)

    def test_ambiguous_answer_persists_and_never_replays_after_restart_or_new_id(self):
        task_id, body = self.question()
        with patch.object(api, 'call', side_effect=api.TransportError('connection_unknown')) as call:
            self.assertEqual(self.request(body, '/' + task_id + '/answer')[0], 200)
            self.request(body, '/' + task_id + '/answer')
            self.assertEqual(len([c for c in call.call_args_list if c.args[1] == 'POST']), 1)
        # A later identical question is conservatively kept closed, even with a fresh UUID.
        state = self.request(suffix='/' + task_id)[1]['data']
        self.assertEqual(state['answer_submission']['status'], 'unknown')
        self.assertEqual(self.request(dict(body, request_id=str(uuid.uuid4())), '/' + task_id + '/answer')[0], 409)

    def test_answer_owner_stale_question_phone_confirmation_and_cancel_guards(self):
        task_id, body = self.question()
        self.assertEqual(self.request(body, '/' + task_id + '/answer', headers={'X-Test-User':'2'})[0], 404)
        self.assertEqual(self.request(dict(body, question_key='a'*64), '/' + task_id + '/answer')[0], 409)
        with self.sessions() as s:
            s.get(AiTask, task_id).status = 'waiting_on_phone'
        self.assertEqual(self.request(body, '/' + task_id + '/answer')[0], 409)
        with self.sessions() as s:
            row = s.get(AiTask, task_id)
            row.status, row.cancel_requested = 'waiting_for_answer', True
        self.assertEqual(self.request(body, '/' + task_id + '/answer')[0], 409)
        self.assertFalse(any(c[2].endswith('/answer') for c in self.calls))


if __name__ == '__main__':
    unittest.main()
