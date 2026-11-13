import importlib.util
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('bk_ai_phone', Path(__file__).resolve().parents[1] / 'ops/bk_ai_phone.py')
phone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone)


@unittest.skipUnless(os.name == 'posix', 'Phone file locks require POSIX; run in existing Linux container')
class PhoneArbitrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bk-ai-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        phone.activate_guard(self.root)
        self.task = str(uuid.uuid4())
        self.request = dict(method='POST', path='/api/v1/tasks', body={'request_id': self.task, 'goal': 'read only'},
                            info=phone.DEFAULT_INFO.replace('bk101/', 'bk101.mcp/'), state_dir=self.root)

    def test_unknown_submission_is_not_replayed(self):
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}) as api:
            phone.bridge(self.request)
            phone.bridge(self.request)
        self.assertEqual([c.args[1] for c in api.call_args_list], ['POST', 'GET'])
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)

    def test_duplicate_semantic_conflicts_never_reach_phone(self):
        request = dict(self.request, body=dict(self.request['body'],
            execution={'target_duration_s': 7200}, budget={'max_requests': 30}))
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}) as api:
            phone.bridge(request)
            for changed in (dict(request['body'], goal='different'),
                            dict(request['body'], execution={'target_duration_s': 60}),
                            dict(request['body'], budget={'max_requests': 31})):
                reply = phone.bridge(dict(request, body=changed))
                self.assertEqual(reply['body']['error']['code'], 'conflict')
            self.assertEqual(api.call_count, 1)
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)
        self.assertNotIn('read only', (Path(self.root) / 'state.json').read_text())

    def test_semantic_signature_ignores_json_key_order(self):
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}) as api:
            phone.bridge(self.request)
            phone.bridge(dict(self.request, body={'goal': 'read only', 'request_id': self.task}))
        self.assertEqual([call.args[1] for call in api.call_args_list], ['POST', 'GET'])

    def test_legacy_unresolved_reservation_is_queryable_not_resubmitted(self):
        with phone.state(self.root) as data:
            data['ai'] = self.task
        with patch.object(phone, 'api', return_value={'http': 200, 'body': {'task_id': self.task, 'status': 'running'}}) as api:
            reply = phone.bridge(self.request)
            self.assertEqual(reply['body']['error']['code'], 'request_signature_unavailable')
            api.assert_not_called()
            phone.bridge(dict(self.request, method='GET', path='/api/v1/tasks/' + self.task, body=None))
            api.assert_called_once()

    def test_wait_does_not_hold_control_lock_and_cancel_acceptance_keeps_lease(self):
        with phone.state(self.root) as data:
            data['ai'] = self.task
        started, release = threading.Event(), threading.Event()
        def api(info, method, path, body=None):
            if path.endswith('/wait'):
                started.set()
                release.wait(3)
                return {'http': 204, 'body': {}}
            return {'http': 202, 'body': {'accepted': True}}
        waiting = dict(self.request, path='/api/v1/tasks/' + self.task + '/wait',
                       body={'after_revision': 0, 'timeout_ms': 20000})
        cancel = dict(self.request, path='/api/v1/tasks/' + self.task + '/cancel', body={})
        other = dict(self.request, body={'request_id': str(uuid.uuid4()), 'goal': 'another'})
        with patch.object(phone, 'api', side_effect=api), ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(phone.bridge, waiting)
            try:
                self.assertTrue(started.wait(1))
                self.assertEqual(pool.submit(phone.bridge, cancel).result(timeout=1)['http'], 202)
                self.assertEqual(pool.submit(phone.bridge, other).result(timeout=1)['http'], 409)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=1)['http'], 204)
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)

    def test_contracted_terminal_requires_finished_receipt_on_submit_and_query(self):
        execution = {'version': 1, 'duration_s': 7200}
        request = dict(self.request, body=dict(self.request['body'], execution=execution))
        fake = {'task_id': self.task, 'status': 'cancelled', 'execution': execution}
        with patch.object(phone, 'api', return_value={'http': 201, 'body': fake}):
            phone.bridge(request)
        query = dict(self.request, method='GET', path='/api/v1/tasks/' + self.task, body=None)
        invalid = [fake,
            dict(fake, execution={'version': 1, 'duration_s': 60}, execution_result={'execution_finished': True, 'finished_at_ms': 10}),
            dict(fake, execution_result={'execution_finished': False, 'finished_at_ms': 10}),
            dict(fake, execution_result={'execution_finished': True, 'finished_at_ms': True}),
            dict(fake, status='done', execution_result={'execution_finished': True, 'finished_at_ms': 10,
                                                      'duration_reached': False, 'business_success': True})]
        for body in invalid:
            with patch.object(phone, 'api', return_value={'http': 200, 'body': body}):
                phone.bridge(query)
            with phone.state(self.root) as data:
                self.assertEqual(data['ai'], self.task)
                self.assertEqual(data['ai_execution'], execution)
        actual = dict(fake, execution_result={'execution_finished': True, 'finished_at_ms': 10})
        with patch.object(phone, 'api', return_value={'http': 200, 'body': actual}):
            phone.bridge(query)
        with phone.state(self.root) as data:
            self.assertIsNone(data['ai'])

    def test_unconfirmed_contracted_terminal_blocks_manual_takeover(self):
        execution = {'version': 1, 'duration_s': 7200}
        with phone.state(self.root) as data:
            data['ai'] = self.task
            data['ai_execution'] = execution
        clock = iter([0, 0, 11])
        fake = {'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled', 'execution': execution}}
        with patch.object(phone.time, 'monotonic', side_effect=lambda: next(clock)), \
             patch.object(phone.time, 'sleep'), patch.object(phone, 'api', return_value=fake):
            with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                phone.enter_manual(self.root)
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)
            self.assertEqual(data['manual'], [])

    def test_confirmed_contracted_terminal_allows_manual_takeover(self):
        execution = {'version': 1, 'duration_s': 7200}
        with phone.state(self.root) as data:
            data['ai'] = self.task
            data['ai_execution'] = execution
        actual = {'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled', 'execution': execution,
                  'execution_result': {'execution_finished': True, 'finished_at_ms': 10}}}
        with patch.object(phone, 'api', return_value=actual):
            claim = phone.enter_manual(self.root)
        with phone.state(self.root) as data:
            self.assertIsNone(data['ai'])
            self.assertIn(claim, data['manual'])
        phone.leave_manual(self.root, claim)

    def test_terminal_for_a_different_requested_task_never_releases_lease(self):
        with phone.state(self.root) as data:
            data['ai'] = self.task
        query = dict(self.request, method='GET', path='/api/v1/tasks/' + str(uuid.uuid4()), body=None)
        with patch.object(phone, 'api', return_value={'http': 200, 'body': {'task_id': self.task, 'status': 'done'}}):
            phone.bridge(query)
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)

    def test_invalid_request_id_is_definite_refusal(self):
        for value in (None, '', 'not-a-uuid', '0' * 36, 1):
            with patch.object(phone, 'api') as api:
                reply = phone.bridge(dict(self.request, body={'request_id': value, 'goal': 'read'}))
                self.assertEqual(reply['http'], 400)
                api.assert_not_called()

    def test_admission_diagnostic_excludes_goal_and_credentials_and_never_calls_api(self):
        request = dict(self.request, info='secret-location', body=dict(self.request['body'], goal='private goal'))
        with patch.object(phone, 'api') as api:
            phone.record_admission(request, {'http':409,'body':{'error':{'code':'device_not_ready','message':'private detail'}}})
            api.assert_not_called()
        receipt = json.loads((Path(self.root)/'last-admission.json').read_text())
        self.assertEqual(receipt['task_id'], self.task)
        self.assertEqual(receipt['error'], 'device_not_ready')
        self.assertNotIn('private', json.dumps(receipt))
        self.assertNotIn('secret', json.dumps(receipt))

    def test_second_task_blocked_until_confirmed_terminal(self):
        with patch.object(phone, 'api', return_value={'http': 201, 'body': {'task_id': self.task, 'status': 'starting'}}):
            phone.bridge(self.request)
        other = dict(self.request, body={'request_id': str(uuid.uuid4()), 'goal': 'other'})
        with patch.object(phone, 'api') as api:
            self.assertEqual(phone.bridge(other)['http'], 409)
            api.assert_not_called()
        with patch.object(phone, 'api', return_value={'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled'}}):
            phone.bridge(dict(self.request, method='GET', path='/api/v1/tasks/' + self.task, body=None))
        with phone.state(self.root) as data:
            self.assertIsNone(data['ai'])

    def test_manual_waits_for_terminal_and_blocks_new_ai(self):
        with phone.state(self.root) as data:
            data['ai'] = self.task
        replies = [
            {'http': 200, 'body': {'task_id': self.task, 'status': 'running'}},
            {'http': 202, 'body': {'accepted': True}},
            {'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled'}},
        ]
        with patch.object(phone, 'api', side_effect=replies), patch.object(phone.time, 'sleep'):
            claim = phone.enter_manual(self.root)
        with patch.object(phone, 'api') as api:
            self.assertEqual(phone.bridge(self.request)['http'], 409)
            api.assert_not_called()
        phone.leave_manual(self.root, claim)

    def test_cancel_accepted_does_not_allow_input(self):
        with phone.state(self.root) as data:
            data['ai'] = self.task
        clock = iter([0, 0, 11])
        with patch.object(phone.time, 'monotonic', side_effect=lambda: next(clock)), patch.object(phone.time, 'sleep'), \
             patch.object(phone, 'api', return_value={'http': 202, 'body': {'accepted': True}}):
            with self.assertRaises(RuntimeError):
                phone.enter_manual(self.root)
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)
            self.assertEqual(data['manual'], [])

    def test_guard_process_loss_fails_closed(self):
        with patch.object(phone, 'process_identity', return_value=None), patch.object(phone, 'api') as api:
            self.assertEqual(phone.bridge(self.request)['http'], 409)
            api.assert_not_called()

    def test_definite_auth_refusal_releases_reservation(self):
        with patch.object(phone, 'api', return_value={'http': 401, 'body': {}}):
            self.assertEqual(phone.bridge(self.request)['http'], 401)
        with phone.state(self.root) as data:
            self.assertIsNone(data['ai'])

    def test_restart_retains_unresolved_task(self):
        with phone.state(self.root) as data:
            data['ai'] = self.task
            data['manual'] = ['old-controller']
        phone.activate_guard(self.root)
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)
            self.assertEqual(data['manual'], [])

    def test_no_arbitrary_api_or_url(self):
        self.assertEqual(phone.api('/unused', 'GET', 'https://example.com')['http'], 400)
        self.assertEqual(phone.api('/unused', 'POST', '/api/v1/device/unlock', {})['http'], 400)

    def test_disconnected_socket_releases_even_without_close_callback(self):
        control, peer = socket.socketpair()
        self.addCleanup(peer.close)
        self.addCleanup(control.close)
        with control:
            claim = phone.enter_manual(self.root, socket_fd=control.fileno())
            with phone.state(self.root) as data:
                self.assertEqual(data['manual'][0]['id'], claim)
            with patch.object(phone, 'api') as api:
                self.assertEqual(phone.bridge(self.request)['http'], 409)
                api.assert_not_called()
        with phone.state(self.root) as data:
            self.assertEqual(data['manual'], [])

    def test_immediate_terminal_submission_releases_reservation(self):
        with patch.object(phone, 'api', return_value={'http':201,'body':{'task_id':self.task,'status':'done'}}):
            phone.bridge(self.request)
        with phone.state(self.root) as data:
            self.assertIsNone(data['ai'])

    def test_preparation_failure_never_submits_and_releases_lease(self):
        with patch.object(phone, 'prepare_unsecured', return_value=False), patch.object(phone, 'api') as api:
            reply = phone.bridge(dict(self.request, prepare_unsecured=True))
            self.assertEqual(reply['http'], 409)
            api.assert_not_called()
        with phone.state(self.root) as data:
            self.assertIsNone(data['ai'])

    def test_preparation_is_opt_in_and_not_repeated_for_duplicate(self):
        with patch.object(phone, 'prepare_unsecured', return_value=True) as prepare, \
             patch.object(phone, 'api', return_value={'http':201,'body':{'task_id':self.task,'status':'starting'}}):
            request = dict(self.request, prepare_unsecured=True)
            phone.bridge(request)
            phone.bridge(request)
            prepare.assert_called_once_with(self.request['info'])

    def test_manual_takeover_uses_durable_package_binding_after_lost_acceptance(self):
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}):
            phone.bridge(self.request)
        with phone.state(self.root) as data:
            self.assertEqual(data['ai_info'], self.request['info'])
        replies = [
            {'http': 200, 'body': {'task_id': self.task, 'status': 'running'}},
            {'http': 202, 'body': {'accepted': True}},
            {'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled'}},
        ]
        with patch.object(phone, 'api', side_effect=replies) as api, patch.object(phone.time, 'sleep'):
            claim = phone.enter_manual(self.root)
        self.assertEqual([call.args[0] for call in api.call_args_list], [self.request['info']] * 3)
        with phone.state(self.root) as data:
            self.assertIsNone(data['ai'])
            self.assertNotIn('ai_info', data)
        phone.leave_manual(self.root, claim)

    def test_duplicate_lookup_releases_only_confirmed_terminal_without_reexecution(self):
        execution = {'version': 1, 'duration_s': 120}
        request = dict(self.request, body=dict(self.request['body'], execution=execution))
        terminal = {'task_id': self.task, 'status': 'cancelled', 'execution': execution}
        actual = dict(terminal, execution_result={'execution_finished': True, 'finished_at_ms': 10})
        replies = [{'http': 502, 'body': {}}, {'http': 200, 'body': terminal}, {'http': 200, 'body': actual}]
        with patch.object(phone, 'api', side_effect=replies) as api:
            phone.bridge(request)
            phone.bridge(request)
            with phone.state(self.root) as data:
                self.assertEqual(data['ai'], self.task)
            phone.bridge(request)
        self.assertEqual([call.args[1] for call in api.call_args_list], ['POST', 'GET', 'GET'])
        with phone.state(self.root) as data:
            self.assertIsNone(data['ai'])
            self.assertNotIn('ai_info', data)

    def test_other_package_cannot_retry_query_cancel_wait_or_answer_active_task(self):
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}):
            phone.bridge(self.request)
        methods = [('POST', '/api/v1/tasks', self.request['body']),
                   ('GET', '/api/v1/tasks/' + self.task, None),
                   ('POST', '/api/v1/tasks/' + self.task + '/cancel', {}),
                   ('POST', '/api/v1/tasks/' + self.task + '/wait', {'after_revision': 0, 'timeout_ms': 1000}),
                   ('POST', '/api/v1/tasks/' + self.task + '/answer',
                    {'text': 'reply', 'question_key': '0' * 64, 'request_id': str(uuid.uuid4())})]
        with patch.object(phone, 'api') as api:
            for method, path, body in methods:
                reply = phone.bridge(dict(self.request, method=method, path=path, body=body, info=phone.DEFAULT_INFO))
                self.assertEqual(reply['http'], 409)
                self.assertEqual(reply['body']['error']['code'], 'conflict')
            with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                phone.enter_manual(self.root, info=phone.DEFAULT_INFO)
            api.assert_not_called()
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)
            self.assertEqual(data['manual'], [])

    def test_invalid_new_or_persisted_package_is_never_used_or_rebound(self):
        with patch.object(phone, 'api') as api:
            self.assertEqual(phone.bridge(dict(self.request, info='/tmp/credential'))['http'], 400)
            api.assert_not_called()
        for invalid in (None, '/tmp/credential', self.request['info'] + '/..'):
            with phone.state(self.root) as data:
                data.update(ai=self.task, ai_info=invalid)
            with patch.object(phone, 'api') as api:
                with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                    phone.enter_manual(self.root)
                reply = phone.bridge(dict(self.request, method='GET', path='/api/v1/tasks/' + self.task, body=None))
                self.assertEqual(reply['http'], 409)
                api.assert_not_called()
            with phone.state(self.root) as data:
                self.assertEqual(data['ai'], self.task)
                self.assertEqual(data['manual'], [])

    def test_manual_terminal_for_changed_binding_does_not_release_replacement(self):
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}):
            phone.bridge(self.request)
        def changed(*args):
            with phone.state(self.root) as data:
                data['ai_info'] = phone.DEFAULT_INFO
            return {'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled'}}
        with patch.object(phone, 'api', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'Task changed'):
                phone.enter_manual(self.root)
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)
            self.assertEqual(data['ai_info'], phone.DEFAULT_INFO)
            self.assertEqual(data['manual'], [])

    def test_query_terminal_for_changed_binding_does_not_release_replacement(self):
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}):
            phone.bridge(self.request)
        def changed(*args):
            with phone.state(self.root) as data:
                data['ai_execution'] = {'version': 1, 'duration_s': 120}
            return {'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled'}}
        with patch.object(phone, 'api', side_effect=changed):
            phone.bridge(dict(self.request, method='GET', path='/api/v1/tasks/' + self.task, body=None))
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)

    def test_concurrent_confirmed_release_keeps_live_manual_takeover_valid(self):
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}):
            phone.bridge(self.request)
        def already_released(*args):
            with phone.state(self.root) as data:
                phone.release_ai(data)
            return {'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled'}}
        with patch.object(phone, 'api', side_effect=already_released):
            claim = phone.enter_manual(self.root)
        with phone.state(self.root) as data:
            self.assertTrue(phone.has_manual(data, claim))
            self.assertIsNone(data['ai'])
        phone.leave_manual(self.root, claim)

    def test_guard_restart_during_takeover_cannot_return_an_unowned_manual_claim(self):
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}):
            phone.bridge(self.request)
        def restarted(*args):
            phone.activate_guard(self.root)
            return {'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled'}}
        with patch.object(phone, 'api', side_effect=restarted):
            with self.assertRaisesRegex(RuntimeError, 'Manual claim changed'):
                phone.enter_manual(self.root)
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)
            self.assertEqual(data['manual'], [])

    def test_legacy_manual_record_uses_original_default_without_rebinding(self):
        with phone.state(self.root) as data:
            data['ai'] = self.task
        with patch.object(phone, 'api', return_value={'http': 200, 'body': {'task_id': self.task, 'status': 'cancelled'}}) as api:
            claim = phone.enter_manual(self.root)
        self.assertEqual(api.call_args.args[0], phone.DEFAULT_INFO)
        phone.leave_manual(self.root, claim)

    def answer_request(self):
        with phone.state(self.root) as data:
            data['ai'] = self.task
        question = dict(task_id=self.task, status='waiting_for_answer', answerable=True,
                        question='继续阅读？', options=['继续', '停止'], conversation='a', started_at_ms=1)
        body = dict(text='继续', question_key=phone.question_key(question), request_id=str(uuid.uuid4()))
        return dict(self.request, path='/api/v1/tasks/' + self.task + '/answer', body=body), question

    def test_answer_forwards_only_text_once_even_after_unknown_response(self):
        request, question = self.answer_request()
        with patch.object(phone, 'api', side_effect=[{'http':200,'body':question},{'http':502,'body':{}}]) as api:
            self.assertEqual(phone.bridge(request)['http'], 502)
            self.assertEqual(phone.bridge(request)['http'], 409)
            self.assertEqual(api.call_count, 2)
            self.assertEqual(api.call_args_list[-1].args[3], {'text':'继续'})
        self.assertNotIn('继续', (Path(self.root)/'state.json').read_text())

    def test_answer_checks_live_question_and_never_approves_on_phone_confirmation(self):
        for value in (dict(status='waiting_on_phone', answerable=False), dict(status='waiting_for_answer', answerable=True, question='Other?')):
            request, question = self.answer_request()
            with phone.state(self.root) as data:
                data['answer_attempts'] = {}
            with patch.object(phone, 'api', return_value={'http':200,'body':dict(question, **value)}) as api:
                self.assertEqual(phone.bridge(request)['http'], 409)
                self.assertEqual([c.args[1] for c in api.call_args_list], ['GET'])

    def test_answer_manual_owner_blocks_before_api(self):
        request, _ = self.answer_request()
        with phone.state(self.root) as data:
            data['manual'] = ['controller']
        with patch.object(phone, 'api') as api:
            self.assertEqual(phone.bridge(request)['http'], 409)
            api.assert_not_called()


class EventWaitHttpTests(unittest.TestCase):
    def setUp(self):
        test = self
        self.received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                test.received.append((self.path, body))
                self.send_response(204)
                self.end_headers()
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"execution_versions":[1],"task_wait_revision":true,"terminal_evidence":true}')
            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.temp = tempfile.TemporaryDirectory(prefix='bk-wait-api-')
        self.info = Path(self.temp.name) / 'connection.json'
        self.info.write_text(json.dumps({'port': self.http.server_port, 'token': 'synthetic-test-token'}))
        self.path = '/api/v1/tasks/' + str(uuid.uuid4()) + '/wait'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_wait_empty_204_is_not_delivery_failure_and_capabilities_are_read_only(self):
        body = {'after_revision': 4, 'timeout_ms': 20000}
        self.assertEqual(phone.api(str(self.info), 'POST', self.path, body), {'http': 204, 'body': {}})
        self.assertEqual(self.received, [(self.path, body)])
        capabilities = phone.api(str(self.info), 'GET', '/api/v1/capabilities')
        self.assertEqual(capabilities['body']['execution_versions'], [1])
        self.assertEqual(phone.api(str(self.info), 'POST', '/api/v1/capabilities', {})['http'], 400)

    def test_wait_schema_bounds_reject_before_delivery(self):
        for body in (None, {}, {'after_revision': 0, 'timeout_ms': 999},
                     {'after_revision': 0, 'timeout_ms': 20001},
                     {'after_revision': -1, 'timeout_ms': 1000},
                     {'after_revision': True, 'timeout_ms': 1000},
                     {'after_revision': 0, 'timeout_ms': 1000, 'unexpected': 1}):
            self.assertEqual(phone.api(str(self.info), 'POST', self.path, body)['http'], 400)
        self.assertEqual(self.received, [])
        self.assertEqual(phone.api(str(self.info), 'GET', self.path)['http'], 400)


class PreparationTests(unittest.TestCase):
    def test_ready_requires_no_secure_lock_and_live_helper(self):
        for device, ready in [({'secure':False,'ready':True,'helper_available':True}, True),
                              ({'secure':True,'ready':True,'helper_available':True}, False),
                              ({'secure':False,'ready':False,'helper_available':True}, False),
                              ({'secure':False,'ready':True,'helper_available':False}, False), ({}, False)]:
            with patch.object(phone.subprocess, 'run') as run, \
                 patch.object(phone, 'api', return_value={'http':200,'body':{'device':device}}):
                self.assertIs(phone.prepare_unsecured('/sdcard/Android/data/com.beyondchenlin.bk101/files/mcp-usb.json'), ready)
                self.assertTrue(all('shell' not in c.kwargs for c in run.call_args_list))

    def test_command_timeout_does_not_submit(self):
        with patch.object(phone.subprocess, 'run', side_effect=phone.subprocess.TimeoutExpired('wm',3)), \
             patch.object(phone, 'api') as api:
            self.assertFalse(phone.prepare_unsecured('/sdcard/Android/data/com.beyondchenlin.bk101/files/mcp-usb.json'))
            api.assert_not_called()


class EvidenceBatchTests(unittest.TestCase):
    def setUp(self):
        self.eid = str(uuid.uuid4())
        self.image = b'\xff\xd8' + b'x' * (414953 - 4) + b'\xff\xd9'
        self.digest = hashlib.sha256(self.image).hexdigest()
        self.request = dict(method='GET', path='/api/v1/evidence/' + self.eid + '/chunks/0',
            info=phone.DEFAULT_INFO.replace('bk101/', 'bk101.mcp/'), state_dir='/not-used')

    def local_chunk(self, info, method, path, body=None):
        self.assertEqual(info, self.request['info'])
        self.assertEqual(method, 'GET')
        self.assertIsNone(body)
        self.assertTrue(path.startswith('/api/v1/evidence/' + self.eid + '/chunk/'))
        offset = int(path.rsplit('/', 1)[1])
        return {'http': 200, 'body': dict(evidence_id=self.eid, offset=offset,
            size=len(self.image), sha256=self.digest,
            data=base64.b64encode(self.image[offset:offset + phone.EVIDENCE_CHUNK]).decode(),
            ignored_phone_field='must-not-leave-the-phone')}

    def test_four_local_reads_share_one_bridge_request_without_state_lock_or_secret_export(self):
        with patch.object(phone, 'api', side_effect=self.local_chunk) as api, patch.object(phone, 'state') as state:
            reply = phone.bridge(self.request)
        self.assertEqual(reply['http'], 200)
        self.assertEqual(api.call_count, 4)
        state.assert_not_called()
        self.assertLess(len(json.dumps(reply).encode()), 1024 * 1024)
        self.assertNotIn('must-not-leave-the-phone', json.dumps(reply))
        self.assertNotIn('info', reply)
        self.assertEqual(b''.join(base64.b64decode(c['data']) for c in reply['body']['chunks']), self.image)

    def test_largest_image_is_limited_to_four_chunks_per_request(self):
        self.image = b'\xff\xd8' + b'x' * (phone.EVIDENCE_MAX_IMAGE - 4) + b'\xff\xd9'
        self.digest = hashlib.sha256(self.image).hexdigest()
        with patch.object(phone, 'api', side_effect=self.local_chunk) as api:
            first = phone.bridge(self.request)
            self.assertEqual(api.call_count, 4)
            second = phone.bridge(dict(self.request, path=self.request['path'][:-1] + str(4 * phone.EVIDENCE_CHUNK)))
            self.assertEqual(api.call_count, 6)
        self.assertEqual([len(r['body']['chunks']) for r in (first, second)], [4, 2])
        self.assertTrue(all(len(json.dumps(r).encode()) < 1024 * 1024 for r in (first, second)))

    def test_write_operation_injection_invalid_paths_and_offsets_do_not_reach_api(self):
        variants = [dict(self.request, method='POST'), dict(self.request, body={}),
            dict(self.request, body={'method': 'POST', 'path': '/api/v1/tasks'}),
            dict(self.request, path=self.request['path'].replace('/evidence/', '/tasks/')),
            dict(self.request, path=self.request['path'][:-1] + '1'),
            dict(self.request, path=self.request['path'][:-1] + str(phone.EVIDENCE_MAX_IMAGE)),
            dict(self.request, path=self.request['path'] + '?method=POST'),
            dict(self.request, info='/another-app/access.json')]
        with patch.object(phone, 'api') as api:
            for request in variants:
                with self.subTest(request=request):
                    self.assertEqual(phone.bridge(request)['http'], 400)
            api.assert_not_called()

    def test_mixed_identity_digest_offset_or_oversized_chunk_fails_whole_batch(self):
        mutations = [dict(evidence_id=str(uuid.uuid4())), dict(sha256='0' * 64),
            dict(offset=0), dict(size=phone.EVIDENCE_MAX_IMAGE + 1), dict(data='A' * (phone.EVIDENCE_CHUNK * 2))]
        for changes in mutations:
            with self.subTest(changes=list(changes)):
                def corrupt(info, method, path, body=None):
                    reply = self.local_chunk(info, method, path, body)
                    if reply['body']['offset']:
                        reply['body'].update(changes)
                    return reply
                with patch.object(phone, 'api', side_effect=corrupt):
                    reply = phone.bridge(self.request)
                self.assertEqual(reply['http'], 502)
                self.assertNotIn('chunks', reply['body'])

    def test_loopback_failure_and_deadline_do_not_return_partial_success(self):
        def unavailable(info, method, path, body=None):
            if path.endswith('/0'):
                return self.local_chunk(info, method, path, body)
            return {'http': 502, 'body': {}}
        with patch.object(phone, 'api', side_effect=unavailable) as api:
            self.assertEqual(phone.bridge(self.request)['http'], 502)
            self.assertEqual(api.call_count, 2)
        with patch.object(phone, 'api', side_effect=self.local_chunk) as api, \
             patch.object(phone.time, 'monotonic', side_effect=[0, 0, 14]):
            reply = phone.bridge(self.request)
            self.assertEqual(reply['body']['error']['code'], 'evidence_batch_deadline')
            self.assertEqual(api.call_count, 1)

    def test_capability_is_bridge_envelope_metadata_only_after_authenticated_list_success(self):
        request = dict(self.request, path='/api/v1/evidence')
        body = {'evidence': [], 'bridge': {'evidence_chunk_batch_version': 999}}
        with patch.object(phone, 'api', return_value={'http': 200, 'body': body}):
            reply = phone.bridge(request)
        self.assertEqual(reply['bridge'], {'evidence_chunk_batch_version': 1})
        self.assertIs(reply['body'], body)
        with patch.object(phone, 'api', return_value={'http': 401, 'body': {'error': {'code': 'unauthorized'}}}):
            self.assertNotIn('bridge', phone.bridge(request))


if __name__ == '__main__':
    unittest.main()
