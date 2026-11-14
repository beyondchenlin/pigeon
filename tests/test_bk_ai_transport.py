import importlib.util
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

if importlib.util.find_spec('lamda') is None:
    raise unittest.SkipTest('Run transport checks in the existing PIGEON runtime')
spec = importlib.util.spec_from_file_location('bk_ai_transport', Path(__file__).resolve().parents[1] / 'server/bk_ai_transport.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


class TransportTests(unittest.TestCase):
    def test_shared_default_configuration_and_explicit_override(self):
        common = dict(python='/runtime/python3', library='/runtime/lib', bridge='/user/bridge.py',
                      info='auto', state_dir='/user/state')
        special = dict(common, info='/phone/legacy.json')
        with tempfile.TemporaryDirectory(prefix='bk-mapping-') as directory:
            path = Path(directory) / 'devices.json'
            path.write_text(json.dumps({'_default': common, 'legacy': special}), encoding='utf-8')
            with patch.dict(os.environ, {'BK_AI_DEVICES_FILE': str(path)}):
                self.assertEqual(common, transport.mapping('newdevice'))
                self.assertEqual(special, transport.mapping('legacy'))
                path.write_text(json.dumps({'_default': dict(common, bridge='relative')}), encoding='utf-8')
                with self.assertRaises(transport.TransportError):
                    transport.mapping('newdevice')

    def invoke(self, method, path, warmup_error=False, body=None, response=None, **options):
        events = []
        device = MagicMock()
        self.device = device
        def info():
            events.append('readonly_ui_init')
            if warmup_error:
                raise RuntimeError('unavailable')
        device.device_info.side_effect = info
        def execute(*args, **kwargs):
            events.append('bridge')
            return SimpleNamespace(stdout=response if response is not None else json.dumps({'http':200,'body':{}}).encode())
        device.execute_script.side_effect = execute
        config = dict(python='/runtime/python3', library='/runtime/lib', bridge='/user/bridge.py', info='/phone/info.json', state_dir='/user/state')
        with patch('lamda.client.Device', return_value=device), patch('grpc.intercept_channel', side_effect=lambda channel, _: channel), \
             patch.object(transport, 'mapping', return_value=config), patch.dict(os.environ, {'API_PORT':'65000'}):
            if warmup_error:
                with self.assertRaises(transport.TransportError):
                    transport.call('example', method, path, body, **options)
            else:
                transport.call('example', method, path, body, **options)
        device.channel.close.assert_called_once()
        return events

    def test_initialize_readonly_ui_before_creation_only(self):
        self.assertEqual(self.invoke('POST', '/api/v1/tasks'), ['readonly_ui_init','bridge'])
        self.assertEqual(self.invoke('GET', '/api/v1/tasks/id'), ['bridge'])
        self.assertEqual(self.invoke('GET', '/api/v1/native-discovery', discover_native=True), ['readonly_ui_init','bridge'])

    def test_failed_initialization_never_creates_task(self):
        self.assertEqual(self.invoke('POST', '/api/v1/tasks', warmup_error=True), ['readonly_ui_init'])

    def test_event_wait_has_its_own_bounded_budget_without_ui_initialization(self):
        path = '/api/v1/tasks/12345678-1234-1234-1234-123456789abc/wait'
        self.assertEqual(self.invoke('POST', path, body={'after_revision': 3, 'timeout_ms': 20000}), ['bridge'])
        self.assertEqual(self.device.execute_script.call_args.kwargs['timeout'], 28)
        self.assertIs(type(self.device.execute_script.call_args.kwargs['timeout']), int)
        self.invoke('POST', path, body={'after_revision': 3, 'timeout_ms': 1501})
        self.assertEqual(self.device.execute_script.call_args.kwargs['timeout'], 10)
        self.assertEqual(self.invoke('POST', path.replace('/wait', '/cancel'), body={}), ['bridge'])
        self.assertEqual(self.device.execute_script.call_args.kwargs['timeout'], 12)
        self.assertEqual(self.invoke('GET', '/api/v1/capabilities'), ['bridge'])
        self.assertEqual(self.device.execute_script.call_args.kwargs['timeout'], 12)

    def test_invalid_wait_never_opens_connection(self):
        path = '/api/v1/tasks/12345678-1234-1234-1234-123456789abc/wait'
        for body in (None, {}, {'after_revision': -1, 'timeout_ms': 1000},
                     {'after_revision': 0, 'timeout_ms': 20001},
                     {'after_revision': 0, 'timeout_ms': 999},
                     {'after_revision': True, 'timeout_ms': 1000},
                     {'after_revision': 0, 'timeout_ms': 1000, 'extra': 1}):
            with patch('lamda.client.Device') as device:
                with self.assertRaisesRegex(transport.TransportError, 'invalid_wait'):
                    transport.call('example', 'POST', path, body)
                device.assert_not_called()

    def test_evidence_batch_has_a_bounded_read_budget_without_ui_initialization(self):
        path = '/api/v1/evidence/12345678-1234-1234-1234-123456789abc/chunks/0'
        self.assertEqual(self.invoke('GET', path), ['bridge'])
        self.assertEqual(self.device.execute_script.call_args.kwargs['timeout'], 18)
        self.assertEqual(self.invoke('GET', path.replace('/chunks/', '/chunk/')), ['bridge'])
        self.assertEqual(self.device.execute_script.call_args.kwargs['timeout'], 12)

    def test_batch_rejects_writes_custom_operations_and_unbounded_offsets_before_connecting(self):
        path = '/api/v1/evidence/12345678-1234-1234-1234-123456789abc/chunks/0'
        for method, target, body in [('POST', path, None), ('GET', path, {}),
                ('GET', path, {'method': 'POST', 'path': '/api/v1/tasks'}),
                ('GET', path[:-1] + '1', None), ('GET', path[:-1] + str(768 * 1024), None),
                ('GET', path.replace('/evidence/', '/tasks/'), None)]:
            with self.subTest(method=method, path=target), patch('lamda.client.Device') as device:
                with self.assertRaisesRegex(transport.TransportError, 'invalid_evidence_batch'):
                    transport.call('example', method, target, body)
                device.assert_not_called()

    def test_batch_preserves_one_mebibyte_reply_limit_and_closes_channel(self):
        path = '/api/v1/evidence/12345678-1234-1234-1234-123456789abc/chunks/0'
        with self.assertRaisesRegex(transport.TransportError, 'invalid_response'):
            self.invoke('GET', path, response=b'x' * (1024 * 1024 + 1))
        self.device.channel.close.assert_called_once()
