"""Synthetic gateway authentication and fail-closed admission tests."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application
from executor.models import Device, Task, User, UserDevice
from server.bk_ai_store import AiTask
from server.bk_native_gateway import NativeGatewayConnection
from server.bk_native_reload import NativeReload, reserve
from server import bk_native_gateway_api as gateway


class GatewayApiTests(AsyncHTTPTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bk-gateway-test-')
        self.addCleanup(self.tmp.cleanup)
        self.secret = Path(self.tmp.name) / 'secret'
        self.secret.write_text('a' * 64, encoding='ascii')
        self.secret.chmod(0o600)
        self.env = patch.dict(os.environ, {'BK_GATEWAY_SECRET_FILE': str(self.secret)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        for table in (User.__table__, Device.__table__, UserDevice.__table__, Task.__table__,
                      AiTask.__table__, NativeGatewayConnection.__table__, NativeReload.__table__):
            table.create(self.engine)
        self.addCleanup(self.engine.dispose)
        sessions = sessionmaker(self.engine, expire_on_commit=False)
        @contextmanager
        def scope():
            with sessions() as session, session.begin():
                yield session
        self.sessions = scope
        with scope() as session:
            session.add(User(id=1, name='owner'))
            session.add(Device(id=1, domain='phonea', state='online'))
            session.add(UserDevice(user_id=1, device_id=1))
        self.scope_patch = patch.object(gateway, 'session_scope', scope)
        self.scope_patch.start(); self.addCleanup(self.scope_patch.stop)
        self.worker_patch = patch.object(gateway, 'worker_matches', return_value=True)
        self.worker_patch.start(); self.addCleanup(self.worker_patch.stop)
        super().setUp()

    def get_app(self):
        return Application([(r'/__bk_gateway', gateway.GatewayAdmissionHandler)])

    def request_gate(self, action, identity, key=None):
        body = {'action':action, 'domain':'phonea', 'connection_id':identity}
        if action == 'open': body.update(worker_pid=42, worker_start='123')
        return self.fetch('/__bk_gateway', method='POST',
            headers={'X-BK-Gateway-Key': key or ''},
            body=json.dumps(body))

    def test_secret_and_device_connection_exclude_reload(self):
        identity = str(uuid.uuid4())
        self.assertEqual(self.request_gate('open', identity).code, 403)
        with self.sessions() as session:
            self.assertEqual(session.query(NativeGatewayConnection).count(), 0)
        self.assertEqual(self.request_gate('open', identity, 'a'*64).code, 204)
        with self.sessions() as session:
            with self.assertRaisesRegex(ValueError, 'device_has_gateway_connection'):
                reserve(session, 1, 1, str(uuid.uuid4()))
        self.assertEqual(self.request_gate('close', identity, 'a'*64).code, 204)
        self.assertEqual(self.request_gate('close', identity, 'a'*64).code, 204)
        with self.sessions() as session:
            reserve(session, 1, 1, str(uuid.uuid4()))
        self.assertEqual(self.request_gate('open', str(uuid.uuid4()), 'a'*64).code, 409)

    def test_unsafe_secret_fails_closed(self):
        self.secret.chmod(0o644)
        self.assertEqual(self.request_gate('open', str(uuid.uuid4()), 'a'*64).code, 503)
