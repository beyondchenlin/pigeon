import importlib.util
import json
import os
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
    def invoke(self, method, path, warmup_error=False):
        events = []
        device = MagicMock()
        def info():
            events.append('readonly_ui_init')
            if warmup_error:
                raise RuntimeError('unavailable')
        device.device_info.side_effect = info
        def execute(*args, **kwargs):
            events.append('bridge')
            return SimpleNamespace(stdout=json.dumps({'http':200,'body':{}}).encode())
        device.execute_script.side_effect = execute
        config = dict(python='/runtime/python3', library='/runtime/lib', bridge='/user/bridge.py', info='/phone/info.json', state_dir='/user/state')
        with patch('lamda.client.Device', return_value=device), patch('grpc.intercept_channel', side_effect=lambda channel, _: channel), \
             patch.object(transport, 'mapping', return_value=config), patch.dict(os.environ, {'API_PORT':'65000'}):
            if warmup_error:
                with self.assertRaises(transport.TransportError):
                    transport.call('example', method, path)
            else:
                transport.call('example', method, path)
        device.channel.close.assert_called_once()
        return events

    def test_initialize_readonly_ui_before_creation_only(self):
        self.assertEqual(self.invoke('POST', '/api/v1/tasks'), ['readonly_ui_init','bridge'])
        self.assertEqual(self.invoke('GET', '/api/v1/tasks/id'), ['bridge'])

    def test_failed_initialization_never_creates_task(self):
        self.assertEqual(self.invoke('POST', '/api/v1/tasks', warmup_error=True), ['readonly_ui_init'])
