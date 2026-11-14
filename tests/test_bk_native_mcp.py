import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
import bk_native_mcp as mcp
import bk_ai_phone as phone


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.task = str(uuid.uuid4())
        self.calls = []
        self.result = {'task_id': self.task, 'status': 'running'}

    def transport(self, request):
        self.calls.append(request)
        result = ({'protocolVersion': mcp.PROTOCOL} if request['method'] == 'initialize'
                  else {'isError': False, 'structuredContent': self.result})
        return {'jsonrpc': '2.0', 'id': request['id'], 'result': result}

    def api(self, method='GET', path=None, body=None, transport=None):
        return mcp.task_api('unused', method, path or '/api/v1/tasks/' + self.task, body,
                            transport_factory=lambda _: transport or self.transport)

    def test_submission_preserves_stable_id_and_conversation(self):
        body = {'goal': 'Read only', 'request_id': self.task, 'conversation': 'mcp-' + self.task}
        self.assertEqual(200, self.api('POST', '/api/v1/tasks', body)['http'])
        self.assertEqual('mika_run_task', self.calls[1]['params']['name'])
        self.assertEqual(body, self.calls[1]['params']['arguments'])
        self.assertEqual(['initialize', 'tools/call'], [c['method'] for c in self.calls])

    def test_lost_submission_response_never_retries(self):
        def lose(request):
            if request['method'] == 'tools/call':
                self.calls.append(request)
                raise TimeoutError('do not expose raw error')
            return self.transport(request)
        result = self.api('POST', '/api/v1/tasks', {'goal': 'Read only', 'request_id': self.task}, lose)
        self.assertEqual(mcp.failure('delivery_unknown'), result)
        self.assertEqual(2, len(self.calls))

    def test_tool_error_cannot_be_misread_as_safe_to_resubmit(self):
        def refuse(request):
            reply = self.transport(request)
            if request['method'] == 'tools/call':
                reply['result'] = {'isError': True, 'content': [{'type': 'text', 'text': 'private error'}]}
            return reply
        self.assertEqual(mcp.failure('mcp_tool_result_unknown'), self.api(transport=refuse))

    def test_wrong_task_result_is_unknown(self):
        self.result['task_id'] = str(uuid.uuid4())
        self.assertEqual(mcp.failure('mcp_task_identity_mismatch'), self.api())

    def test_wrong_rpc_id_is_rejected(self):
        def mismatch(request):
            reply = self.transport(request)
            reply['id'] += 1
            return reply
        self.assertEqual(mcp.failure('invalid_mcp_response'), self.api(transport=mismatch))

    def test_cancel_acceptance_is_not_a_terminal_task(self):
        self.result = {'task_id': self.task, 'cancelled': True}
        result = self.api('POST', '/api/v1/tasks/' + self.task + '/cancel', {})
        self.assertTrue(result['body']['accepted'])
        self.assertNotIn('status', result['body'])

    def test_invalid_or_missing_submission_id_does_not_connect(self):
        for identity in (None, 'not-a-uuid', self.task.upper()):
            with self.subTest(identity=identity):
                reply = self.api('POST', '/api/v1/tasks', {'goal': 'Read only', 'request_id': identity})
                self.assertEqual(400, reply['http'])
        self.assertEqual([], self.calls)

    def test_arbitrary_tools_or_paths_do_not_connect(self):
        for path in ('/mcp', 'https://example.com', '/api/v1/device/unlock'):
            self.assertEqual(400, self.api('POST', path, {})['http'])
        self.assertEqual([], self.calls)

    def test_standard_text_result_is_supported(self):
        def text_result(request):
            reply = self.transport(request)
            if request['method'] == 'tools/call':
                reply['result'] = {'isError': False, 'content': [{'type': 'text', 'text': json.dumps(self.result)}]}
            return reply
        self.assertEqual(self.result, self.api(transport=text_result)['body'])

    def test_loopback_config_cannot_choose_remote_url(self):
        with tempfile.TemporaryDirectory(prefix='bk-mcp-config-') as directory:
            info = Path(directory) / 'info.json'
            info.write_text(json.dumps({'port': 8765, 'token': 'test-token', 'url': 'https://evil.invalid'}))
            transport = mcp.LocalTransport(info)
            self.assertEqual('http://127.0.0.1:8765/mcp', transport.url)
            info.write_text(json.dumps({'port': True, 'token': 'test-token'}))
            with self.assertRaises(ValueError):
                mcp.LocalTransport(info)

    def test_response_size_is_bounded_and_redirect_not_followed(self):
        with tempfile.TemporaryDirectory(prefix='bk-mcp-config-') as directory:
            info = Path(directory) / 'info.json'
            info.write_text(json.dumps({'port': 8765, 'token': 'test-token'}))
            transport = mcp.LocalTransport(info)
            response = Mock(status=200)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = b' ' * (mcp.MAX_RESPONSE + 1)
            with patch.object(transport.opener, 'open', return_value=response):
                with self.assertRaises(mcp.McpError):
                    transport({'id': 1})
            response.read.assert_called_once_with(mcp.MAX_RESPONSE + 1)
            redirect = next(h for h in transport.opener.handlers if isinstance(h, mcp.urllib.request.HTTPRedirectHandler))
            self.assertIsNone(redirect.redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))

    def test_new_package_routes_via_mcp(self):
        info = phone.DEFAULT_INFO.replace('bk101/', 'bk101.nativemcp/')
        self.assertTrue(phone.valid_info(info))
        self.assertFalse(phone.valid_info(info.replace('nativemcp', 'unlisted')))
        with patch.object(mcp, 'task_api', return_value={'http': 200, 'body': {}}) as call:
            phone.api(info, 'GET', '/api/v1/device')
        call.assert_called_once_with(info, 'GET', '/api/v1/device', None)
        self.assertEqual(('mika_device_state', {}, None), mcp.task_operation('GET', '/api/v1/device', None))

    def test_stock_phone_rejects_strict_task_before_connect(self):
        body = {'goal': 'Browse', 'request_id': self.task, 'execution': {'kind': 'browse'}}
        self.assertEqual(mcp.failure('unsupported_execution', 400), self.api('POST', '/api/v1/tasks', body))
        self.assertFalse(self.calls)

    def test_probe_checks_identity_and_required_tools_without_exposing_config(self):
        required = ['mika_run_task', 'mika_task_status', 'mika_list_tasks',
                    'mika_cancel_task', 'mika_answer', 'mika_device_state']
        def transport(request):
            self.calls.append(request['method'])
            result = ({'protocolVersion': mcp.PROTOCOL, 'serverInfo': {
                'name': 'mika', 'title': 'BK CLAW', 'version': '0.1.42-mcp.1'}}
                if request['method'] == 'initialize' else
                {'tools': [{'name': name} for name in required]})
            return {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        with tempfile.TemporaryDirectory(prefix='bk-probe-') as directory:
            info = Path(directory) / 'config.json'
            info.write_text('{"token":"secret"}')
            result = mcp.probe(str(info), transport_factory=lambda _: transport,
                               package_check=lambda _: True)
            self.assertEqual({'state': 'connected', 'version': '0.1.42-mcp.1'}, result)
            self.assertEqual(['initialize', 'tools/list'], self.calls)
            self.assertNotIn('secret', str(result))
            required.remove('mika_cancel_task')
            self.assertEqual('incompatible', mcp.probe(str(info), transport_factory=lambda _: transport,
                                                     package_check=lambda _: True)['state'])
            self.assertEqual('not_installed', mcp.probe(str(info), package_check=lambda _: False)['state'])
            self.assertEqual('not_ready', mcp.probe(str(info / 'absent'), package_check=lambda _: True)['state'])

    def test_probe_reports_auth_failure_without_exposing_error(self):
        def denied(_):
            raise mcp.McpError('private-token', 401)
        with tempfile.TemporaryDirectory(prefix='bk-probe-') as directory:
            info = Path(directory) / 'info.json'
            info.write_text('{}')
            self.assertEqual({'state': 'auth_failed'}, mcp.probe(str(info),
                transport_factory=lambda _: denied, package_check=lambda _: True))

    def test_probe_package_list_distinguishes_missing_from_similar_package(self):
        with patch.object(mcp.subprocess, 'run', return_value=Mock(returncode=0,
                stdout=b'package:com.beyondchenlin.bk101.nativemcp.other\n')):
            self.assertEqual({'state': 'not_installed'}, mcp.probe('/does/not/matter'))

    @unittest.skipIf(sys.platform == 'win32', 'phone bridge locking requires Linux or Android')
    def test_discovery_bridge_is_read_only_and_limited_to_native_package(self):
        info = '/sdcard/Android/data/com.beyondchenlin.bk101.nativemcp/files/mcp-usb.json'
        with tempfile.TemporaryDirectory(prefix='bk-discovery-') as directory:
            with patch.object(mcp, 'probe', return_value={'state': 'connected', 'version': '44'}) as probe, \
                 patch.object(phone, 'process_identity', return_value='alive'):
                request = {'state_dir': directory, 'info': info, 'method': 'GET', 'path': '/api/v1/native-discovery'}
                self.assertEqual('guard_not_ready', phone.bridge(request)['body']['state'])
                with phone.state(directory) as data:
                    data.update(guard_pid=100, guard_identity='alive', guard_protocol={'version': 2, 'pid': 100, 'identity': 'alive'})
                self.assertEqual({'state': 'connected', 'version': '44'}, phone.bridge(request)['body'])
                # An old guard restarted after a new one must not inherit the marker.
                with phone.state(directory) as data:
                    data.update(guard_pid=101)
                self.assertEqual('guard_not_ready', phone.bridge(request)['body']['state'])
                self.assertEqual(3, probe.call_count)
            result = phone.bridge(dict(request, info=phone.DEFAULT_INFO))
            self.assertEqual(400, result['http'])

    @unittest.skipIf(sys.platform == 'win32', 'phone bridge locking requires Linux or Android')
    def test_auto_binding_persists_across_completion_and_never_resubmits(self):
        with tempfile.TemporaryDirectory(prefix='bk-auto-bind-') as directory:
            with phone.state(directory) as data:
                data.update(guard_pid=100, guard_identity='alive', guard_protocol={'version': 2, 'pid': 100, 'identity': 'alive'}, manual=[])
            request = {'state_dir': directory, 'info': 'auto', 'method': 'POST',
                       'path': '/api/v1/tasks', 'body': {'goal': 'calculate', 'request_id': self.task}}
            def fake_api(info, method, path, body=None):
                self.calls.append((info, method, path))
                return {'http': 200, 'body': {'task_id': self.task, 'status': 'done'}}
            with patch.object(mcp, 'probe', return_value={'state': 'connected'}), \
                 patch.object(phone, 'process_identity', return_value='alive'), \
                 patch.object(phone, 'api', side_effect=fake_api):
                self.assertEqual(200, phone.bridge(request)['http'])
                self.assertEqual(200, phone.bridge(request)['http'])
                query = dict(request, method='GET', path='/api/v1/tasks/' + self.task, body=None)
                self.assertEqual(200, phone.bridge(query)['http'])
                altered = dict(request, body={'goal': 'different', 'request_id': self.task})
                self.assertEqual(409, phone.bridge(altered)['http'])
            self.assertEqual(['POST', 'GET', 'GET'], [call[1] for call in self.calls])
            self.assertTrue(all(call[0] == phone.NATIVE_INFO for call in self.calls))
            with phone.state(directory) as data:
                self.assertEqual(phone.NATIVE_INFO, data['task_info'][self.task])

    @unittest.skipIf(sys.platform == 'win32', 'phone bridge locking requires Linux or Android')
    def test_auto_binding_rejects_unknown_task_without_guessing_package(self):
        with tempfile.TemporaryDirectory(prefix='bk-auto-bind-') as directory:
            request = {'state_dir': directory, 'info': 'auto', 'method': 'GET',
                       'path': '/api/v1/tasks/' + self.task}
            self.assertEqual('task_package_unknown', phone.bridge(request)['body']['error']['code'])

    @unittest.skipIf(sys.platform == 'win32', 'phone bridge locking requires Linux or Android')
    def test_auto_binding_lost_submission_is_only_queried(self):
        with tempfile.TemporaryDirectory(prefix='bk-auto-lost-') as directory:
            with phone.state(directory) as data:
                data.update(guard_pid=100, guard_identity='alive', guard_protocol={'version': 2, 'pid': 100, 'identity': 'alive'}, manual=[])
            request = {'state_dir': directory, 'info': 'auto', 'method': 'POST',
                       'path': '/api/v1/tasks', 'body': {'goal': 'calculate', 'request_id': self.task}}
            def fake_api(info, method, path, body=None):
                self.calls.append((info, method))
                return (mcp.failure('delivery_unknown') if method == 'POST' else
                        {'http': 200, 'body': {'task_id': self.task, 'status': 'running'}})
            with patch.object(mcp, 'probe', return_value={'state': 'connected'}), \
                 patch.object(phone, 'process_identity', return_value='alive'), \
                 patch.object(phone, 'api', side_effect=fake_api):
                self.assertEqual(502, phone.bridge(request)['http'])
                self.assertEqual(200, phone.bridge(request)['http'])
            self.assertEqual(['POST', 'GET'], [method for _, method in self.calls])

    @unittest.skipIf(sys.platform == 'win32', 'phone bridge locking requires Linux or Android')
    def test_auto_refuses_old_guard_without_dispatching(self):
        with tempfile.TemporaryDirectory(prefix='bk-old-guard-') as directory:
            with phone.state(directory) as data:
                data.update(guard_pid=100, guard_identity='alive', manual=[])
            request = {'state_dir': directory, 'info': 'auto', 'method': 'POST',
                       'path': '/api/v1/tasks', 'body': {'goal': 'calculate', 'request_id': self.task}}
            with patch.object(mcp, 'probe', return_value={'state': 'connected'}), \
                 patch.object(phone, 'process_identity', return_value='alive'), \
                 patch.object(phone, 'api') as call:
                reply = phone.bridge(request)
            self.assertEqual('guard_upgrade_required', reply['body']['error']['code'])
            call.assert_not_called()

    @unittest.skipIf(sys.platform == 'win32', 'phone bridge locking requires Linux or Android')
    def test_manual_takeover_stops_the_bound_native_task(self):
        with tempfile.TemporaryDirectory(prefix='bk-takeover-') as directory:
            with phone.state(directory) as data:
                data.update(ai=self.task, ai_info=phone.NATIVE_INFO, ai_execution=None, manual=[])
            calls = []
            def fake_api(info, method, path, body=None):
                calls.append((info, method))
                status = 'cancelled' if any(method == 'POST' for _, method in calls) else 'running'
                return {'http': 200, 'body': {'task_id': self.task, 'status': status}}
            with patch.object(phone, 'api', side_effect=fake_api), patch.object(phone.time, 'sleep'):
                claim = phone.enter_manual(directory)
                with phone.state(directory) as data:
                    self.assertIsNone(data['ai'])
                    self.assertIn(claim, data['manual'])
                phone.leave_manual(directory, claim)
            self.assertEqual(['GET', 'POST', 'GET'], [method for _, method in calls])
            self.assertTrue(all(info == phone.NATIVE_INFO for info, _ in calls))


if __name__ == '__main__':
    unittest.main()
