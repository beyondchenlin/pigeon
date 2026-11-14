import os
import unittest
import uuid
from unittest.mock import patch

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from executor.models import Device, Task, User, UserDevice
from server.bk_ai_store import AiTask, reload_busy
from server.bk_native_reload import NativeReload, reserve, begin_dispatch, observe
from server.bk_native_gateway import NativeGatewayConnection, register, unregister, remove_dead_worker_connections


class ReloadReceiptTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        for table in (User.__table__, Device.__table__, UserDevice.__table__, Task.__table__,
                      AiTask.__table__, NativeGatewayConnection.__table__, NativeReload.__table__):
            table.create(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as s:
            s.add(User(id=1, name='owner'))
            s.add(Device(id=1, domain='a1', state='online'))
            s.add(UserDevice(user_id=1, device_id=1))
        self.addCleanup(self.engine.dispose)

    def test_lost_reply_is_durable_and_never_creates_a_replacement(self):
        attempt = str(uuid.uuid4())
        with self.Session.begin() as s:
            row, created = reserve(s, 1, 1, attempt)
            self.assertTrue(created)
            self.assertEqual('reserved', row.phase)
        with self.Session.begin() as s:
            self.assertTrue(begin_dispatch(s, 1, attempt))
        with self.Session.begin() as s:
            observe(s, 1, attempt, 'unknown')
        with self.Session.begin() as s:
            row, created = reserve(s, 1, 1, attempt)
            self.assertFalse(created)
            self.assertEqual('unknown', row.phase)
            self.assertFalse(begin_dispatch(s, 1, attempt))
            with self.assertRaisesRegex(ValueError, 'reload_unconfirmed'):
                reserve(s, 1, 1, str(uuid.uuid4()))
            with self.assertRaisesRegex(ValueError, 'request_may_have_been_sent'):
                observe(s, 1, attempt, 'not_sent')

    def test_ack_is_not_success_without_verification(self):
        attempt = str(uuid.uuid4())
        with self.Session.begin() as s:
            reserve(s, 1, 1, attempt)
            self.assertTrue(begin_dispatch(s, 1, attempt))
            self.assertEqual('awaiting_reconnect', observe(s, 1, attempt, 'accepted').phase)
        with self.Session.begin() as s:
            self.assertEqual('ready', observe(s, 1, attempt, 'verified').phase)

    def test_reservation_blocks_new_work_until_verification(self):
        attempt = str(uuid.uuid4())
        with self.Session.begin() as s:
            self.assertFalse(reload_busy(s, 1))
            reserve(s, 1, 1, attempt)
            self.assertTrue(reload_busy(s, 1))
        with self.Session.begin() as s:
            begin_dispatch(s, 1, attempt)
            observe(s, 1, attempt, 'accepted')
            self.assertTrue(reload_busy(s, 1))
        with self.Session.begin() as s:
            observe(s, 1, attempt, 'verified')
            self.assertFalse(reload_busy(s, 1))

    def test_gateway_connection_and_reload_mutually_exclude_each_other(self):
        attempt, connection = str(uuid.uuid4()), str(uuid.uuid4())
        with self.Session.begin() as s:
            _, created = register(s, 'a1', connection, 42, '123')
            self.assertTrue(created)
            self.assertFalse(register(s, 'a1', connection, 42, '123')[1])
            with self.assertRaisesRegex(ValueError, 'gateway_connection'):
                reserve(s, 1, 1, attempt)
        with self.Session.begin() as s:
            self.assertTrue(unregister(s, 'a1', connection))
            self.assertFalse(unregister(s, 'a1', connection))
            reserve(s, 1, 1, attempt)
        with self.Session.begin() as s:
            with self.assertRaisesRegex(ValueError, 'reload_in_progress'):
                register(s, 'a1', str(uuid.uuid4()), 42, '123')
            observe(s, 1, attempt, 'not_sent')
        with self.Session.begin() as s:
            self.assertTrue(register(s, 'a1', str(uuid.uuid4()), 42, '123')[1])

    def test_rejects_unowned_busy_and_controlling_devices(self):
        with self.Session.begin() as s:
            with self.assertRaisesRegex(ValueError, 'not_authorized'):
                reserve(s, 1, 9, str(uuid.uuid4()))
            s.get(Device, 1).controlling = True
        with self.Session.begin() as s:
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                reserve(s, 1, 1, str(uuid.uuid4()))
            s.get(Device, 1).controlling = False
            s.add(Task(task_id=str(uuid.uuid4()), device_id=1, state='running'))
        with self.Session.begin() as s:
            with self.assertRaisesRegex(ValueError, 'busy'):
                reserve(s, 1, 1, str(uuid.uuid4()))

    def test_only_proven_dead_workers_are_cleaned(self):
        with self.Session.begin() as s:
            register(s, 'a1', str(uuid.uuid4()), 41, '100')
            register(s, 'a1', str(uuid.uuid4()), 42, '200')
        with self.Session.begin() as s, patch('server.bk_native_gateway.worker_gone', side_effect=lambda pid, start: pid == 41):
            self.assertEqual(remove_dead_worker_connections(s, 1), 1)
            self.assertEqual(s.query(NativeGatewayConnection).one().worker_pid, 42)
            with self.assertRaisesRegex(ValueError, 'gateway_connection'):
                reserve(s, 1, 1, str(uuid.uuid4()))
