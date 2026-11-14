"""Run in the existing PIGEON runtime; SQLite receipts and a fake phone only."""
from contextlib import contextmanager
import json
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch
import importlib.util
import os
import time
import copy
import asyncio
import threading
from pathlib import Path
if importlib.util.find_spec('sqlalchemy') is None or 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run API integration tests in the existing PIGEON runtime')
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tornado.testing import AsyncHTTPTestCase, gen_test
from tornado.httpclient import HTTPRequest
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
        async def observe_events():
            pass
        def original_start(manager):
            asyncio.set_event_loop(uvloop.new_event_loop())
            manager.ioloop = IOLoop.current()
            manager.ioloop.call_later(3, manager.ioloop.stop)
            manager.ioloop.start()
        try:
            with patch.object(api, 'poll', observe), patch.object(api, 'event_poll', observe_events):
                api.start_with_polling(http, original_start)
            self.assertEqual(seen, [http.ioloop.asyncio_loop])
            self.assertIs(BaseAsyncIOLoop.start, previous)
        finally:
            if hasattr(http, '_bk_ai_poll'):
                http._bk_ai_poll.stop()
            if hasattr(http, '_bk_ai_event_poll'):
                http._bk_ai_event_poll.stop()
            if hasattr(http, '_bk_fleet_poll'):
                http._bk_fleet_poll.stop()
            if hasattr(http, 'ioloop'):
                http.ioloop.close(all_fds=True)
            asyncio.set_event_loop(None)


class NativeDiscoveryTests(AsyncHTTPTestCase):
    def get_app(self):
        class Handler(api.NativeDiscoveryHandler):
            def get_login_user_device(self, domain):
                if self.request.headers.get('X-Test-Auth') != 'owner':
                    self.throw(401, 'AUTH', 'unauthorized')
                return SimpleNamespace(id=1), SimpleNamespace(state='offline' if domain == 'offline' else 'online')
        return Application([(r'/api/v1/device/([a-z0-9]+)/ai/native-discovery', Handler)], ctl=None)

    def test_discovery_requires_authorization_and_reports_offline(self):
        with patch.object(api, 'call') as call:
            self.assertEqual(401, self.fetch('/api/v1/device/phonea/ai/native-discovery').code)
            response = self.fetch('/api/v1/device/offline/ai/native-discovery', headers={'X-Test-Auth': 'owner'})
            self.assertEqual('offline', json.loads(response.body)['data']['state'])
            call.assert_not_called()

    def test_discovery_only_returns_public_status(self):
        with patch.object(api, 'call', return_value={'http': 200, 'body': {
                'state': 'connected', 'version': '44', 'token': 'private'}}) as call:
            response = self.fetch('/api/v1/device/phonea/ai/native-discovery', headers={'X-Test-Auth': 'owner'})
            self.assertEqual({'state': 'connected', 'version': '44'}, json.loads(response.body)['data'])
            call.assert_called_once_with('phonea', 'GET', '/api/v1/native-discovery', discover_native=True)


class AiApiTests(AsyncHTTPTestCase):
    def test_native_task_binding_survives_mapping_change(self):
        calls = []
        def native_call(domain, method, path, body=None, **kwargs):
            calls.append((method, path, kwargs))
            if kwargs.get('discover_native'):
                return {'http': 200, 'body': {'state': 'connected'}}
            if method == 'POST' and path == '/api/v1/tasks':
                return {'http': 200, 'body': {'task_id': body['request_id'], 'status': 'running'}}
            return {'http': 200, 'body': {'task_id': path.split('/')[4], 'status': 'running'}}
        with patch.object(api, 'mapping', return_value={'info': 'auto'}), \
             patch.object(api, 'call', side_effect=native_call):
            _, (code, response) = self.submit()
        self.assertEqual(202, code)
        task_id = response['data']['task_id']
        with self.sessions() as s:
            self.assertEqual(api.NATIVE_INFO, s.get(AiTask, task_id).sync_meta['phone_info'])
        with patch.object(api, 'mapping', return_value={'info': '/old/path'}), \
             patch.object(api, 'call', side_effect=native_call):
            api.reconcile(task_id)
        self.assertTrue(any(options.get('create_native') is True for _, _, options in calls))
        self.assertEqual(api.NATIVE_INFO, calls[-1][2].get('task_info'))

    def test_native_not_ready_never_dispatches(self):
        with patch.object(api, 'mapping', return_value={'info': 'auto'}), \
             patch.object(api, 'call', return_value={'http': 200, 'body': {'state': 'guard_not_ready'}}) as call:
            _, (code, response) = self.submit()
        self.assertEqual(202, code)
        self.assertEqual('rejected', response['data']['status'])
        self.assertEqual(1, call.call_count)

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
            if path == '/api/v1/capabilities':
                return dict(http=200, body=dict(execution_versions=[1], task_wait_revision=True, terminal_evidence=True))
            if method == 'POST' and path == '/api/v1/tasks':
                self.remote[body['request_id']] = dict(task_id=body['request_id'], status='running', step=1)
                if 'execution' in body:
                    self.remote[body['request_id']].update(execution=body['execution'], revision=1,
                        event_at_ms=int(time.time()*1000), sampled_at_ms=int(time.time()*1000))
                return dict(http=201, body=self.remote[body['request_id']])
            task_id = path.split('/')[4]
            if path.endswith('/wait'):
                value = self.remote[task_id]
                return dict(http=200, body=value) if value['revision'] > body['after_revision'] else dict(http=204, body={})
            if path.endswith('/answer'):
                self.remote[task_id] = dict(task_id=task_id, status='running')
                return dict(http=200, body=dict(task_id=task_id, accepted=True))
            if path.endswith('/cancel'):
                previous = self.remote[task_id]
                self.remote[task_id] = dict(task_id=task_id, status='cancelled')
                if previous.get('execution'):
                    self.remote[task_id] = dict(previous, status='cancelled', revision=previous['revision']+1,
                        sampled_at_ms=int(time.time()*1000), execution_result=self.finished_result(False))
                return dict(http=202, body={'accepted': True})
            return dict(http=200, body=self.remote[task_id])
        self.patches = [patch.object(api, 'session_scope', self.sessions), patch.object(api, 'mapping', return_value={}),
                        patch.object(api, 'call', side_effect=call), patch('server.bk_evidence.notify_terminal')]
        for p in self.patches:
            p.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in reversed(self.patches):
            p.stop()
        self.engine.dispose()
        api.INFLIGHT.clear()
        api.CANCEL_INFLIGHT.clear()
        api.EVENT_INFLIGHT.clear()

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

    def test_long_budget_boundaries_and_unchanged_default(self):
        for timeout in (15, 7200, 10800, None):
            with self.subTest(timeout=timeout):
                body = dict(goal='Read only', request_id=str(uuid.uuid4()))
                if timeout is not None:
                    body['timeout_s'] = timeout
                code, result = self.request(body)
                self.assertEqual(code, 202)
                row = result['data']
                self.assertEqual(round(row['deadline'] - row['created_at']), timeout or 900)
                self.assertEqual(self.request(body)[1]['data']['task_id'], row['task_id'])
                self.assertEqual(self.request({}, '/' + row['task_id'] + '/cancel')[1]['data']['status'], 'cancelled')
        self.assertEqual(sum(method == 'POST' and path == '/api/v1/tasks' for _, method, path, _ in self.calls), 4)

    def test_invalid_long_budget_rejected_before_phone_contact(self):
        for timeout in (14, 10801, True, 7200.0, '7200', None):
            with self.subTest(timeout=timeout):
                code, _ = self.request(dict(goal='Read only', request_id=str(uuid.uuid4()), timeout_s=timeout))
                self.assertEqual(code, 400)
        self.assertEqual(self.calls, [])

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

    @staticmethod
    def execution():
        return dict(version=1, kind='browse', duration_s=7200, clock='elapsed',
            finish_timeout_s=60, stop_timeout_s=30, progress_interval_s=600, event_latency_s=30,
            budget={'max_tokens': 12000}, progress_requirements={
                'min_observations': 2, 'min_active_s': 3600, 'max_stall_s': 120})

    @staticmethod
    def finished_result(success):
        return dict(execution_finished=True, duration_reached=success, business_success=success,
            finished_at_ms=int(time.time()*1000), reason='duration_reached' if success else 'user_stop',
            evidence={'state':'missing', 'reason':'synthetic_test_no_image'})

    def browse(self):
        body = dict(goal='Browse without posting', request_id=str(uuid.uuid4()), execution=self.execution())
        code, response = self.request(body)
        self.assertEqual(code, 202)
        return body, response['data']

    def test_browse_contract_persisted_default_covers_two_hours_and_old_id_conflicts(self):
        body, row = self.browse()
        self.assertEqual(row['execution'], body['execution'])
        self.assertEqual(round(row['deadline'] - row['created_at']), 7440)
        self.assertEqual(self.calls[1][3]['execution'], body['execution'])
        for path, value in [('duration_s', 7199), ('finish_timeout_s', 59), ('stop_timeout_s', 29),
                            ('progress_interval_s', 599), ('event_latency_s', 29)]:
            modified = copy.deepcopy(body)
            modified['execution'][path] = value
            self.assertEqual(self.request(modified)[0], 409)
        for group, key, value in [('budget', 'max_tokens', 11999),
                                  ('progress_requirements', 'min_observations', 3),
                                  ('progress_requirements', 'min_active_s', 3599),
                                  ('progress_requirements', 'max_stall_s', 119)]:
            modified = copy.deepcopy(body)
            modified['execution'][group][key] = value
            self.assertEqual(self.request(modified)[0], 409)
        self.assertEqual(self.request(body)[1]['data']['task_id'], row['task_id'])
        self.assertEqual(sum(m == 'POST' and p == '/api/v1/tasks' for _,m,p,_ in self.calls), 1)

    def test_invalid_browse_contract_and_short_supervision_never_contact_phone(self):
        body = dict(goal='Browse', request_id=str(uuid.uuid4()), execution=self.execution())
        for field, value in [('clock', 'active'), ('kind', 'publish'), ('duration_s', True),
                              ('version', 2), ('budget', {'max_tokens':0}),
                              ('progress_requirements', {'min_observations':1,'min_active_s':1,'max_stall_s':10})]:
            invalid = copy.deepcopy(body)
            invalid['execution'][field] = value
            self.assertEqual(self.request(invalid)[0], 400)
        self.assertEqual(self.request(dict(body, execution=None))[0], 400)
        self.assertEqual(self.request(dict(body, execution={**body['execution'], 'extra':1}))[0], 400)
        self.assertEqual(self.request(dict(body, timeout_s=7200))[0], 400)
        self.assertEqual(self.calls, [])

    def test_old_phone_capabilities_rejected_before_business_submission(self):
        with patch.object(api, 'call', return_value={'http':404, 'body':{}}) as call:
            _, row = self.browse()
        self.assertEqual(row['status'], 'rejected')
        self.assertEqual(row['error'], 'unsupported_execution_contract')
        self.assertEqual([c.args[1:] for c in call.call_args_list], [('GET', '/api/v1/capabilities')])

    def test_browse_first_receipt_lost_queries_same_identity_without_replay(self):
        real_call = api.call.side_effect
        def lost(domain, method, path, body=None):
            reply = real_call(domain, method, path, body)
            if method == 'POST' and path == '/api/v1/tasks':
                raise api.TransportError('connection_unknown')
            return reply
        with patch.object(api, 'call', side_effect=lost):
            body, row = self.browse()
        self.assertEqual(row['status'], 'unknown')
        task_id = row['task_id']
        self.assertEqual(self.request(body)[1]['data']['task_id'], task_id)
        refreshed = self.request(suffix='/' + task_id + '?refresh=1')[1]['data']
        self.assertEqual(refreshed['status'], 'running')
        self.assertEqual(sum(m == 'POST' and p == '/api/v1/tasks' for _,m,p,_ in self.calls), 1)

    def test_browse_cache_does_not_poll_phone_and_manual_refresh_does(self):
        _, row = self.browse()
        count = len(self.calls)
        self.assertEqual(self.request(suffix='/' + row['task_id'])[0], 200)
        self.assertEqual(self.request(suffix='/' + row['task_id'] + '?refresh=0')[0], 200)
        self.assertEqual(len(self.calls), count)
        self.request(suffix='/' + row['task_id'] + '?refresh=1')
        self.assertEqual(len(self.calls), count + 1)

    def test_event_after_summary_arrives_without_waiting_for_next_progress_period(self):
        _, row = self.browse()
        task_id = row['task_id']
        self.remote[task_id] = dict(self.remote[task_id], status='waiting_on_phone', revision=2,
            event_at_ms=int(time.time()*1000), sampled_at_ms=int(time.time()*1000))
        api.wait_event(task_id)
        result = self.request(suffix='/' + task_id)[1]['data']
        self.assertEqual(result['status'], 'waiting_on_phone')
        self.assertEqual(self.calls[-1][1:3], ('POST', '/api/v1/tasks/' + task_id + '/wait'))
        self.assertIsNotNone(result['source_event_at_ms'])
        with self.sessions() as s:
            self.assertGreater(s.get(AiTask, task_id).sync_meta['next_progress_at'], time.time() + 590)

    def test_empty_event_wait_updates_contact_without_importing_ordinary_progress(self):
        _, row = self.browse()
        task_id = row['task_id']
        self.remote[task_id] = dict(self.remote[task_id], step=50)
        api.wait_event(task_id)
        result = self.request(suffix='/' + task_id)[1]['data']
        self.assertEqual(result['result']['step'], 1)
        self.assertIsNotNone(result['last_contact_at'])

    def test_stop_unconfirmed_keeps_device_reserved_and_independent_deadline_visible(self):
        _, row = self.browse()
        task_id = row['task_id']
        with patch.object(api, 'call', side_effect=lambda d,m,p,b=None: {'http':202,'body':{'accepted':True}}
                          if p.endswith('/cancel') else {'http':200,'body':self.remote[task_id]}):
            code, response = self.request({}, '/' + task_id + '/cancel')
        self.assertEqual(code, 202)
        self.assertTrue(response['data']['cancel_requested'])
        with self.sessions() as s:
            self.assertIsNotNone(active(s, 1))
            task = s.get(AiTask, task_id)
            task.sync_meta = dict(task.sync_meta, cancel_requested_at=time.time()-31)
        self.assertTrue(self.request(suffix='/' + task_id)[1]['data']['stop_confirmation_overdue'])
        self.assertEqual(self.submit()[1][0], 409)
        self.remote[task_id] = dict(self.remote[task_id], status='cancelled', revision=2,
            sampled_at_ms=int(time.time()*1000), execution_result=self.finished_result(False))
        api.apply_reply(task_id, {'http':200,'body':self.remote[task_id]})
        with self.sessions() as s:
            self.assertIsNone(active(s, 1))

    def test_unfinished_or_unsuccessful_phone_terminal_cannot_release_device(self):
        _, row = self.browse()
        task_id = row['task_id']
        bad = dict(self.remote[task_id], status='done', revision=2,
            sampled_at_ms=int(time.time()*1000), execution_result=self.finished_result(False))
        api.apply_reply(task_id, {'http':200,'body':bad})
        result = self.request(suffix='/' + task_id)[1]['data']
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['error'], 'unconfirmed_phone_terminal')
        with self.sessions() as s:
            self.assertIsNotNone(active(s, 1))

    def test_terminal_evidence_wakes_once_after_receipt_commit(self):
        _, row = self.browse()
        task_id = row['task_id']
        committed = []
        original_sessions = self.sessions

        @contextmanager
        def tracked_sessions():
            with original_sessions() as session:
                yield session
            committed.append(True)

        observed = []

        def notified(domain, received_task_id):
            with original_sessions() as session:
                observed.append((bool(committed), domain, received_task_id,
                    session.get(AiTask, task_id).status))

        terminal = dict(self.remote[task_id], status='done', revision=2,
            sampled_at_ms=int(time.time() * 1000) + 1, execution_result=self.finished_result(True))
        with patch.object(api, 'session_scope', tracked_sessions), \
                patch('server.bk_evidence.notify_terminal', side_effect=notified) as wake:
            api.apply_reply(task_id, {'http': 200, 'body': terminal}, source='event')
            api.apply_reply(task_id, {'http': 200, 'body': terminal}, source='event')
            wake.assert_called_once_with('phonea', task_id)
        self.assertEqual(observed, [(True, 'phonea', task_id, 'done')])

    def test_evidence_wake_failure_does_not_fail_confirmed_stop(self):
        _, row = self.browse()
        task_id = row['task_id']
        with patch('server.bk_evidence.notify_terminal', side_effect=RuntimeError('injected wake failure')) as wake, \
                self.assertLogs(level='ERROR'):
            code, result = self.request({}, '/' + task_id + '/cancel')
        self.assertEqual(code, 200)
        self.assertEqual(result['data']['status'], 'cancelled')
        self.assertTrue(result['data']['cancel_requested'])
        wake.assert_called_once_with('phonea', task_id)
        with self.sessions() as session:
            receipt = session.get(AiTask, task_id)
            self.assertEqual(receipt.status, 'cancelled')
            self.assertTrue(receipt.cancel_requested)
            self.assertIsNone(active(session, 1))

    def test_evidence_wake_failure_does_not_fail_terminal_refresh(self):
        _, row = self.browse()
        task_id = row['task_id']
        self.remote[task_id] = dict(self.remote[task_id], status='done', revision=2,
            sampled_at_ms=int(time.time() * 1000) + 1, execution_result=self.finished_result(True))
        with patch('server.bk_evidence.notify_terminal', side_effect=RuntimeError('injected wake failure')) as wake, \
                self.assertLogs(level='ERROR'):
            code, result = self.request(suffix='/' + task_id + '?refresh=1')
        self.assertEqual(code, 200)
        self.assertEqual(result['data']['status'], 'done')
        self.assertFalse(result['data']['cancel_requested'])
        wake.assert_called_once_with('phonea', task_id)

    def test_failed_receipt_transaction_does_not_wake_evidence(self):
        _, row = self.browse()
        task_id = row['task_id']
        original_sessions = self.sessions

        @contextmanager
        def rejected_transaction():
            with original_sessions() as session:
                yield session
                raise RuntimeError('injected receipt commit failure')

        terminal = dict(self.remote[task_id], status='done', revision=2,
            sampled_at_ms=int(time.time() * 1000) + 1, execution_result=self.finished_result(True))
        with patch.object(api, 'session_scope', rejected_transaction), \
                patch('server.bk_evidence.notify_terminal') as wake:
            with self.assertRaisesRegex(RuntimeError, 'injected receipt commit failure'):
                api.apply_reply(task_id, {'http': 200, 'body': terminal}, source='event')
            wake.assert_not_called()
        with original_sessions() as session:
            self.assertEqual(session.get(AiTask, task_id).status, 'running')

    def test_out_of_order_and_duplicate_event_cannot_regress_true_terminal(self):
        _, row = self.browse()
        task_id = row['task_id']
        original = self.remote[task_id]
        newer = dict(original, status='finishing', revision=3, sampled_at_ms=original['sampled_at_ms']+3)
        api.apply_reply(task_id, {'http':200,'body':newer})
        api.apply_reply(task_id, {'http':200,'body':original})
        self.assertEqual(self.request(suffix='/' + task_id)[1]['data']['status'], 'finishing')
        terminal = dict(newer, status='done', revision=4, sampled_at_ms=newer['sampled_at_ms']+1,
                        execution_result=self.finished_result(True))
        api.apply_reply(task_id, {'http':200,'body':terminal})
        api.apply_reply(task_id, {'http':200,'body':original})
        api.apply_reply(task_id, {'http':200,'body':terminal})
        self.assertEqual(self.request(suffix='/' + task_id)[1]['data']['status'], 'done')

    def test_stop_workers_remain_available_when_all_event_workers_are_held(self):
        _, row = self.browse()
        release = threading.Event()
        entered = [threading.Event() for _ in range(4)]
        def hold(index):
            entered[index].set()
            release.wait(5)
        futures = [api.EVENT_POOL.submit(hold, i) for i in range(4)]
        try:
            self.assertTrue(all(event.wait(2) for event in entered))
            code, response = self.request({}, '/' + row['task_id'] + '/cancel')
            self.assertEqual(code, 200)
            self.assertEqual(response['data']['status'], 'cancelled')
        finally:
            release.set()
            for future in futures:
                future.result(timeout=2)

    def test_actual_phone_serializer_terminal_is_accepted_and_preserved(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' / 'bk-browse-contract-v1.json').read_text())
        snapshot = fixture['snapshot']
        now = time.time()
        with self.sessions() as s:
            s.add(AiTask(id=snapshot['task_id'], device_id=1, user_id=1,
                request_id=str(uuid.uuid4()), goal=snapshot['goal'], status='running',
                created=now, updated=now, deadline=now+10800, history=[], snapshot={},
                execution=fixture['execution'], sync_meta={'admitted':True}))
        api.apply_reply(snapshot['task_id'], {'http':200,'body':snapshot}, source='event')
        result = self.request(suffix='/' + snapshot['task_id'])[1]['data']
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['result'],snapshot)
        self.assertEqual(result['source_event_at_ms'],snapshot['event_at_ms'])
        with self.sessions() as s:
            self.assertIsNone(active(s,1))

    @gen_test
    async def test_concurrent_same_submission_creates_only_one_phone_execution(self):
        body = dict(goal='Browse', request_id=str(uuid.uuid4()), execution=self.execution())
        def request():
            return HTTPRequest(self.get_url('/api/v1/device/phonea/ai/tasks'), method='POST',
                headers={'X-Test-Auth':'owner'}, body=json.dumps(body))
        responses = await asyncio.gather(self.http_client.fetch(request()), self.http_client.fetch(request()))
        rows = [json.loads(response.body)['data'] for response in responses]
        self.assertEqual(rows[0]['task_id'], rows[1]['task_id'])
        self.assertEqual(sum(m == 'POST' and p == '/api/v1/tasks' for _,m,p,_ in self.calls), 1)


if __name__ == '__main__':
    unittest.main()
