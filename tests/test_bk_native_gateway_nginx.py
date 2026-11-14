"""Isolated real OpenResty/WebSocket test; no production gateway or device."""
import asyncio
from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import patch

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tornado.httpclient import HTTPClientError
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets
from tornado.web import Application
from tornado.websocket import WebSocketHandler, websocket_connect
from executor.models import Device, Task, User, UserDevice
from server.bk_ai_store import AiTask
from server.bk_native_gateway import NativeGatewayConnection
from server.bk_native_reload import NativeReload, reserve, observe
from server import bk_native_gateway_api as gateway

ROOT = Path(__file__).resolve().parents[1]
NGINX = '/usr/local/openresty/nginx/sbin/nginx'


class Echo(WebSocketHandler):
    def check_origin(self, origin): return True
    def on_message(self, value): self.write_message(value)


class GatewayNginxTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_websocket_excludes_reload_and_failed_close_stays_blocked(self):
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread':False})
        for table in (User.__table__, Device.__table__, UserDevice.__table__, Task.__table__,
                      AiTask.__table__, NativeGatewayConnection.__table__, NativeReload.__table__):
            table.create(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        @contextmanager
        def sessions():
            with factory() as session, session.begin(): yield session
        with sessions() as session:
            session.add(User(id=1, name='owner'))
            session.add(Device(id=1, domain='phonea', state='online'))
            session.add(UserDevice(user_id=1, device_id=1))
        backend = None
        process = None
        ws = None
        temporary = tempfile.TemporaryDirectory(prefix='bk-gateway-nginx-')
        try:
            with nullcontext(temporary.name) as folder:
                root = Path(folder)
                (root / 'logs').mkdir()
                secret = root / 'secret'; secret.write_text('b'*64); secret.chmod(0o600)
                console = root / 'console.lua'; console.write_text('-- synthetic console hook\n')
                app = Application([(r'/__bk_gateway',gateway.GatewayAdmissionHandler), (r'/d/phonea/ws',Echo)])
                sockets = bind_sockets(0, address='127.0.0.1')
                backend_port = sockets[0].getsockname()[1]
                backend = HTTPServer(app); backend.add_sockets(sockets)
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1',0)); proxy_port=probe.getsockname()[1]
                access = ROOT / 'lua/bk_native_gateway_access.lua'
                log = ROOT / 'lua/bk_native_gateway_log.lua'
                conf = root / 'nginx.conf'
                conf.write_text(f'''user root;
worker_processes 1;
daemon off;
pid {root}/nginx.pid;
error_log /dev/null crit;
events {{ worker_connections 64; }}
http {{ access_log off;
 server {{ listen 127.0.0.1:{proxy_port};
  location ~ ^/d/(?<id>[0-9a-z]+)/ {{
   set $bk_gateway_key "";
   set $bk_gateway_secret_file "{secret}";
   set $bk_device_console_file "{console}";
   set $bk_gateway_api "http://127.0.0.1:{backend_port}/__bk_gateway";
   access_by_lua_file {access};
   log_by_lua_file {log};
   proxy_http_version 1.1;
   proxy_set_header Upgrade $http_upgrade;
   proxy_set_header Connection "upgrade";
   proxy_pass http://127.0.0.1:{backend_port};
  }}
  location = /_bk_gateway_internal {{
   internal;
   proxy_pass_request_headers off;
   proxy_set_header Content-Type application/json;
   proxy_set_header X-BK-Gateway-Key $bk_gateway_key;
   proxy_pass http://127.0.0.1:{backend_port}/__bk_gateway;
  }}
 }}
}}
''')
                with patch.dict(os.environ, {'BK_GATEWAY_SECRET_FILE':str(secret)}), \
                     patch.object(gateway, 'session_scope', sessions):
                    process = subprocess.Popen([NGINX, '-p', str(root)+'/', '-c', str(conf)],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    for _ in range(60):
                        try:
                            reader, writer = await asyncio.open_connection('127.0.0.1', proxy_port)
                            writer.close(); await writer.wait_closed(); break
                        except OSError:
                            if process.poll() is not None:
                                self.fail('isolated nginx failed to start: ' + process.stderr.read(4096).decode(errors='replace'))
                            await asyncio.sleep(.05)
                    else: self.fail('isolated nginx did not start')
                    url = f'ws://127.0.0.1:{proxy_port}/d/phonea/ws'
                    ws = await asyncio.wait_for(websocket_connect(url), 5)
                    await ws.write_message('synthetic'); self.assertEqual(await ws.read_message(), 'synthetic')
                    with sessions() as session:
                        self.assertEqual(session.query(NativeGatewayConnection).count(), 1)
                        with self.assertRaisesRegex(ValueError, 'device_has_gateway_connection'):
                            reserve(session, 1, 1, str(uuid.uuid4()))
                    ws.close(); ws = None
                    for _ in range(50):
                        with sessions() as session: remaining=session.query(NativeGatewayConnection).count()
                        if remaining == 0: break
                        await asyncio.sleep(.1)
                    self.assertEqual(remaining, 0, 'closed WebSocket must release its record')
                    with sessions() as session:
                        attempt=str(uuid.uuid4()); reserve(session,1,1,attempt)
                    with self.assertRaises(HTTPClientError) as error:
                        await asyncio.wait_for(websocket_connect(url), 5)
                    self.assertEqual(error.exception.code, 409)
                    with sessions() as session: observe(session,1,attempt,'not_sent')
                    ws = await asyncio.wait_for(websocket_connect(url), 5)
                    with sessions() as session: self.assertEqual(session.query(NativeGatewayConnection).count(), 1)
                    # Make the authenticated close fail without touching any
                    # real service; the registry must then stay fail-closed.
                    secret.chmod(0o644)
                    ws.close(); ws = None
                    await asyncio.sleep(.4)
                    secret.chmod(0o600)
                    with sessions() as session:
                        self.assertEqual(session.query(NativeGatewayConnection).count(), 1)
                        with self.assertRaisesRegex(ValueError, 'device_has_gateway_connection'):
                            reserve(session,1,1,str(uuid.uuid4()))
        finally:
            if ws is not None: ws.close()
            if backend is not None: backend.stop()
            if process is not None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            engine.dispose()
            temporary.cleanup()
