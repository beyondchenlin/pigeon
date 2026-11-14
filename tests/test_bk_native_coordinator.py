"""One-shot onboarding, uncertain outcomes, and fail-closed recovery."""
from contextlib import contextmanager
import os
import unittest
import uuid
from unittest.mock import MagicMock, patch

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from executor.models import Device, Task, User, UserDevice
from server.bk_ai_store import AiTask, reload_busy
from server.bk_native_gateway import NativeGatewayConnection, register
from server.bk_native_reload import NativeReload
from server import bk_native_coordinator as coordinator


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        for table in (User.__table__, Device.__table__, UserDevice.__table__, Task.__table__,
                      AiTask.__table__, NativeGatewayConnection.__table__, NativeReload.__table__):
            table.create(self.engine)
        self.addCleanup(self.engine.dispose)
        factory = sessionmaker(self.engine, expire_on_commit=False)
        @contextmanager
        def scope():
            with factory() as session, session.begin(): yield session
        self.scope = scope
        with scope() as session:
            session.add(User(id=1,name='owner'))
            session.add(Device(id=1,domain='phone1',state='online'))
            session.add(UserDevice(user_id=1,device_id=1))
        self.phone = MagicMock()
        self.phone._acquire_lock.return_value = True
        self.phone.reload.return_value = True
        self.sessions = patch.object(coordinator,'session_scope',scope)
        self.gate = patch.object(coordinator,'check_gateway',return_value=True)
        self.channel = patch.object(coordinator,'open_device',return_value=self.phone)
        self.live_worker = patch('server.bk_native_gateway.worker_gone', return_value=False)
        for item in (self.sessions,self.gate,self.channel,self.live_worker):
            item.start(); self.addCleanup(item.stop)

    def test_reload_once_and_reconcile_same_attempt(self):
        identity=str(uuid.uuid4())
        with patch.object(coordinator,'provision',side_effect=[{'state':'reload_required'},{'state':'connected'}]):
            first=coordinator.onboard('phone1',1,identity)
            self.assertEqual(first['state'],'waiting_reconnect')
            with self.scope() as session: self.assertTrue(reload_busy(session,1))
            second=coordinator.onboard('phone1',1,str(uuid.uuid4()))
        self.assertEqual(second,{'state':'connected','attempt_id':identity})
        self.phone.reload.assert_called_once_with(clean=False)
        self.assertEqual(self.phone._release_lock.call_count, 2)
        with self.scope() as session: self.assertFalse(reload_busy(session,1))

    def test_lost_reload_reply_never_causes_another_reload(self):
        identity=str(uuid.uuid4())
        self.phone.reload.side_effect=TimeoutError()
        with patch.object(coordinator,'provision',side_effect=[{'state':'reload_required'},
                          {'state':'reload_required'},{'state':'connected'}]):
            self.assertEqual(coordinator.onboard('phone1',1,identity)['state'],'waiting_reconnect')
            self.assertEqual(coordinator.onboard('phone1',1,identity)['state'],'waiting_reconnect')
            self.assertEqual(coordinator.onboard('phone1',1,identity)['state'],'connected')
        self.phone.reload.assert_called_once()

    def test_untrusted_gateway_and_active_viewer_never_touch_phone(self):
        identity=str(uuid.uuid4())
        with patch.object(coordinator,'check_gateway',return_value=False):
            self.assertEqual(coordinator.onboard('phone1',1,identity)['state'],'gateway_unavailable')
        with self.scope() as session:
            register(session,'phone1',str(uuid.uuid4()),42,'123')
        self.assertEqual(coordinator.onboard('phone1',1,identity)['state'],'waiting_for_idle')
        self.phone.reload.assert_not_called()
        coordinator.open_device.assert_not_called()

    def test_uncertain_install_stays_blocked_and_is_not_replayed(self):
        identity=str(uuid.uuid4())
        with patch.object(coordinator,'provision',side_effect=TimeoutError()) as provision:
            self.assertEqual(coordinator.onboard('phone1',1,identity)['state'],'install_unconfirmed')
            self.assertEqual(coordinator.onboard('phone1',1,identity)['state'],'install_unconfirmed')
        provision.assert_called_once()
        self.phone.reload.assert_not_called()
        with self.scope() as session: self.assertTrue(reload_busy(session,1))

    def test_disabled_app_never_reloads_and_releases_barrier(self):
        identity=str(uuid.uuid4())
        with patch.object(coordinator,'provision',return_value={'state':'not_ready'}):
            self.assertEqual(coordinator.onboard('phone1',1,identity)['state'],'not_ready')
        self.phone.reload.assert_not_called()
        with self.scope() as session: self.assertFalse(reload_busy(session,1))

    def test_busy_phone_is_not_modified(self):
        self.phone._acquire_lock.return_value = False
        with patch.object(coordinator, 'provision') as provision:
            result = coordinator.onboard('phone1', 1, str(uuid.uuid4()))
        self.assertEqual(result['state'], 'waiting_for_idle')
        provision.assert_not_called()
        self.phone.reload.assert_not_called()
