import os
import unittest
import uuid

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from executor.models import Device, Task, User, UserDevice
from server.bk_ai_store import AiTask
from server.bk_native_reload import NativeReload, reserve, begin_dispatch, observe


class ReloadReceiptTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        for table in (User.__table__, Device.__table__, UserDevice.__table__, Task.__table__,
                      AiTask.__table__, NativeReload.__table__):
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
