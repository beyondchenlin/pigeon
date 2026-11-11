import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))


class GuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        class Handler:
            def __init__(inner):
                inner.ws_connection = types.SimpleNamespace(stream=types.SimpleNamespace(socket=types.SimpleNamespace(fileno=lambda:42),closed=lambda:False))
            def on_message(inner, raw): self.events.append(('input', raw))
            def on_close(inner): self.events.append(('close',))
            def close(inner, **kwargs): self.events.append(('blocked',)); inner.on_close()
        self.bundle = types.ModuleType('lamda.web.bundle')
        self.bundle.__file__ = str(ROOT / 'ops/bk_ai_guard_extension.py')
        for name in ('TouchInputHandler', 'RealTimeCmdHandler', 'CodeDebugeeHandler', 'DebugWebSocketHandler'):
            setattr(self.bundle, name, type(name, (Handler,), {}))
        lamda = types.ModuleType('lamda'); lamda.__version__ = '10.9'
        web = types.ModuleType('lamda.web'); web.bundle = self.bundle; lamda.web = web
        extension = types.ModuleType('lamda.extensions'); extension.BaseHttpExtension = object
        self.modules = patch.dict(sys.modules, {'lamda':lamda,'lamda.web':web,'lamda.web.bundle':self.bundle,'lamda.extensions':extension})
        self.modules.start(); self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location('test_guard_extension', ROOT / 'ops/bk_ai_guard_extension.py')
        self.guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.guard)
        with patch.object(self.guard, 'activate_guard'), patch.dict('os.environ', {'USRDIR':'/unused'}), \
             patch.object(self.guard.hashlib, 'sha256', return_value=types.SimpleNamespace(hexdigest=lambda:'03841b518f9a1308de3af62ae2fddbf4b5c0b918fa4e4aa701c5771a720fd05f')):
            self.guard.install()

    async def test_readonly_monitoring_does_not_take_over(self):
        handler = self.bundle.RealTimeCmdHandler()
        with patch.object(self.guard, 'enter_manual') as enter:
            for raw in ('status', '{"command":"device/info"}', '{"command":"display/list"}'):
                await handler.on_message(raw)
            enter.assert_not_called()
        self.assertEqual(len(self.events), 3)

    async def test_input_waits_for_confirmed_stop_and_release_on_close(self):
        handler = self.bundle.TouchInputHandler()
        def enter(*args, **kwargs):
            self.events.append(('confirmed_stop',)); return 'claim'
        with patch.object(self.guard, 'enter_manual', side_effect=enter), patch.object(self.guard, 'leave_manual') as leave:
            await handler.on_message('down')
            await handler.on_message('up')
            handler.on_close()
            leave.assert_called_once_with(str(Path('/unused') / 'phone-pilot/bk-ai'), 'claim')
        self.assertEqual(self.events[:2], [('confirmed_stop',), ('input', 'down')])

    async def test_unknown_stop_blocks_input(self):
        handler = self.bundle.RealTimeCmdHandler()
        with patch.object(self.guard, 'enter_manual', side_effect=RuntimeError('unknown')):
            await handler.on_message('{"command":"device/input/keycode"}')
        self.assertNotIn('input', [e[0] for e in self.events])
        self.assertIn(('blocked',), self.events)

    async def test_unrelated_type_field_cannot_disguise_input(self):
        handler = self.bundle.RealTimeCmdHandler()
        with patch.object(self.guard, 'enter_manual', side_effect=RuntimeError('unknown')):
            await handler.on_message('{"type":"device/info","command":"device/input/keycode"}')
        self.assertNotIn('input', [e[0] for e in self.events])

    async def test_concurrent_input_acquires_only_one_claim(self):
        handler = self.bundle.TouchInputHandler()
        with patch.object(self.guard, 'enter_manual', return_value='claim') as enter:
            await asyncio.gather(handler.on_message('down'), handler.on_message('up'))
            enter.assert_called_once_with(str(Path('/unused') / 'phone-pilot/bk-ai'), socket_fd=42)

    async def test_socket_closed_during_cancellation_never_delivers_stale_input(self):
        handler = self.bundle.TouchInputHandler()
        def enter(*args, **kwargs):
            handler.ws_connection.stream.closed = lambda: True
            return 'claim'
        with patch.object(self.guard, 'enter_manual', side_effect=enter), patch.object(self.guard, 'leave_manual') as leave:
            await handler.on_message('down')
            leave.assert_called_once()
        self.assertNotIn('input', [e[0] for e in self.events])


if __name__ == '__main__':
    unittest.main()
