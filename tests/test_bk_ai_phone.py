import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import socket

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
                            info='/unused', state_dir=self.root)

    def test_unknown_submission_is_not_replayed(self):
        with patch.object(phone, 'api', return_value={'http': 502, 'body': {}}) as api:
            phone.bridge(self.request)
            phone.bridge(self.request)
        self.assertEqual([c.args[1] for c in api.call_args_list], ['POST', 'GET'])
        with phone.state(self.root) as data:
            self.assertEqual(data['ai'], self.task)

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
            prepare.assert_called_once_with('/unused')

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


class PreparationTests(unittest.TestCase):
    def test_ready_requires_no_secure_lock_and_live_helper(self):
        for device, ready in [({'secure':False,'ready':True,'helper_available':True}, True),
                              ({'secure':True,'ready':True,'helper_available':True}, False),
                              ({'secure':False,'ready':False,'helper_available':True}, False),
                              ({'secure':False,'ready':True,'helper_available':False}, False), ({}, False)]:
            with patch.object(phone.subprocess, 'run') as run, \
                 patch.object(phone, 'api', return_value={'http':200,'body':{'device':device}}):
                self.assertIs(phone.prepare_unsecured('/unused'), ready)
                self.assertTrue(all('shell' not in c.kwargs for c in run.call_args_list))

    def test_command_timeout_does_not_submit(self):
        with patch.object(phone.subprocess, 'run', side_effect=phone.subprocess.TimeoutExpired('wm',3)), \
             patch.object(phone, 'api') as api:
            self.assertFalse(phone.prepare_unsecured('/unused'))
            api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
