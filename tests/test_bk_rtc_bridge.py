"""Authorization/lifetime tests without installing FIRERPA on the developer PC."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ops'))
stub = types.ModuleType('lamda.extensions'); stub.BaseHttpExtension = object
with patch.dict(sys.modules, {'lamda.extensions': stub}):
    spec = importlib.util.spec_from_file_location('bk_bridge_test_module', ROOT/'ops/bk_rtc_extension.py')
    ext = importlib.util.module_from_spec(spec); spec.loader.exec_module(ext)


class Channel:
    readyState = 'open'
    bufferedAmount = 0
    def __init__(self): self.events = {}; self.sent = []
    def on(self, name, fn): self.events[name] = fn
    def send(self, raw): self.sent.append(json.loads(raw))
    def close(self): self.readyState = 'closed'


class Socket:
    def __init__(self): self.sent = []; self.closed = False
    def write_message(self, raw):
        self.sent.append(json.loads(raw))
        future = asyncio.get_running_loop().create_future(); future.set_result(None)
        return future
    def close(self): self.closed = True


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dc = Channel()
        self.bridge = ext.TouchBridge(types.SimpleNamespace(request=types.SimpleNamespace(headers={})), self.dc)
    async def asyncTearDown(self):
        self.bridge.close()
        await asyncio.sleep(0)

    async def test_unclaimed_input_is_rejected_and_cannot_open_native_socket(self):
        self.bridge.message(json.dumps(dict(type='touch', seq=1, action='down', x=1, y=1)))
        self.assertTrue(self.bridge.closed)
        self.assertIsNone(self.bridge.socket)

    async def test_claim_without_existing_authenticated_token_fails_closed(self):
        # Missing auth must fail before attempting any local connection.
        fake_http = types.ModuleType('tornado.httpclient'); fake_http.HTTPRequest = object
        fake_ws = types.ModuleType('tornado.websocket')
        fake_ws.websocket_connect = lambda *args, **kwargs: self.fail('Must not open a socket')
        with patch.dict(sys.modules, {'tornado.httpclient': fake_http, 'tornado.websocket': fake_ws}):
            with self.assertRaisesRegex(ValueError, 'authenticated'):
                await self.bridge.connect()

    async def test_disconnect_releases_contact_and_cancels_writer(self):
        socket = Socket(); self.bridge.socket = socket
        self.bridge.protocol.configure(1080, 2388)
        self.bridge.task(self.bridge.writer())
        self.bridge.message(json.dumps(dict(type='touch', seq=1, action='down', x=10, y=20)))
        await asyncio.sleep(.01)
        self.assertEqual(socket.sent[0]['action'], 'down')
        self.assertIn({'type': 'ack', 'seq': 1}, self.dc.sent)
        self.bridge.close(); await asyncio.sleep(0)
        self.assertEqual(socket.sent[-1]['action'], 'up')
        self.assertTrue(socket.closed)
        self.assertEqual(self.dc.readyState, 'closed')

    async def test_expired_gesture_is_dropped_instead_of_replayed(self):
        socket = Socket(); self.bridge.socket = socket
        self.bridge.queue.put_nowait((ext.time.monotonic()-1, 1, {'action':'down','id':0,'x':10,'y':20}))
        await self.bridge.writer()
        self.assertTrue(self.bridge.closed)
        self.assertFalse(any(m['action']=='down' for m in socket.sent))

    async def test_lease_expiry_closes_stuck_contact(self):
        socket = Socket(); self.bridge.socket = socket
        self.bridge.last_seen = ext.time.monotonic()-3
        await asyncio.sleep(.55)
        self.assertTrue(self.bridge.closed)
        self.assertEqual(socket.sent[-1]['action'], 'up')

if __name__ == '__main__': unittest.main()
