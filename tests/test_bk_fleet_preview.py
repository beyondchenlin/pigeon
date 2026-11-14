"""Preview must never expose or select another user's devices."""
from contextlib import contextmanager
import json
import os
from types import SimpleNamespace
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
from executor.models import Device, DeviceJob, Group, Job, Task, User, UserDevice
from server.bk_ai_store import AiTask
from server.bk_native_reload import NativeReload
from server import bk_fleet
from server.bk_fleet_store import FleetDraft
from server import service


class PreviewTests(AsyncHTTPTestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        for table in (User.__table__, Device.__table__, Group.__table__, UserDevice.__table__,
                      Job.__table__, DeviceJob.__table__, Task.__table__, AiTask.__table__, FleetDraft.__table__, NativeReload.__table__):
            table.create(self.engine)
        factory = sessionmaker(self.engine, expire_on_commit=False)

        @contextmanager
        def sessions():
            with factory() as session, session.begin():
                yield session

        self.sessions = sessions
        with sessions() as session:
            session.add_all([User(id=1, name='owner'), User(id=2, name='other'),
                Group(id=1, owner_id=1, name='我的组'), Group(id=2, owner_id=2, name='别人的组'),
                Device(id=1, domain='phonea', comment='我的手机'),
                Device(id=2, domain='phoneb', comment='别人的手机'),
                UserDevice(user_id=1, device_id=1, group_id=1),
                UserDevice(user_id=2, device_id=2, group_id=2)])
        self.patch = patch.object(bk_fleet, 'session_scope', self.sessions)
        self.patch.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.patch.stop()
        self.engine.dispose()

    def get_app(self):
        class Handler(bk_fleet.FleetPlanPreviewHandler):
            def get_login_user(self, session=None):
                if self.request.headers.get('X-Test-Auth') != 'owner':
                    self.throw(401, 'AUTH', 'unauthorized')
                return SimpleNamespace(id=1)
        class JobHandler(bk_fleet.FleetJobHandler):
            def get_login_user(self, session=None):
                if self.request.headers.get('X-Test-Auth') != 'owner':
                    self.throw(401, 'AUTH', 'unauthorized')
                return SimpleNamespace(id=1)
        return Application([(r'/preview', Handler), (r'/jobs(?:/([0-9]+)(?:/(cancel))?)?', JobHandler)], ctl=None)

    def preview(self, kind, name, auth='owner'):
        body = {'goal': '读取状态', 'target': {'kind': kind, 'name': name}, 'schedule': {'kind': 'now'}}
        response = self.fetch('/preview', method='POST', body=json.dumps(body), headers={'X-Test-Auth': auth})
        return response.code, json.loads(response.body)

    def test_owner_gets_preview_and_no_dispatch(self):
        status, body = self.preview('group', '我的组')
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['plan']['devices'], ['phonea'])
        self.assertFalse(body['data']['submitted'])

    def test_other_users_group_and_device_cannot_be_resolved(self):
        for kind, name in [('group', '别人的组'), ('device', 'phoneb')]:
            with self.subTest(kind=kind):
                status, body = self.preview(kind, name)
                self.assertEqual(status, 400)
                self.assertNotIn('phoneb', json.dumps(body))

    def test_unauthenticated_is_rejected(self):
        status, _ = self.preview('group', '我的组', auth='')
        self.assertEqual(status, 401)

    def test_preview_then_submit_is_idempotent(self):
        _, preview = self.preview('group', '我的组')
        body = {'request_id': str(uuid.uuid4()), 'plan': preview['data']['plan']}
        def submit():
            response = self.fetch('/jobs', method='POST', body=json.dumps(body), headers={'X-Test-Auth': 'owner'})
            return response.code, json.loads(response.body)
        first, second = submit(), submit()
        self.assertEqual(first[0], 202)
        self.assertEqual(second[0], 202)
        self.assertEqual(first[1]['data']['id'], second[1]['data']['id'])
        with self.sessions() as session:
            self.assertEqual(session.query(Job).count(), 1)

    def test_changed_membership_after_preview_blocks_submit(self):
        _, preview = self.preview('group', '我的组')
        with self.sessions() as session:
            session.query(UserDevice).filter_by(user_id=1, device_id=1).delete()
        body = {'request_id': str(uuid.uuid4()), 'plan': preview['data']['plan']}
        response = self.fetch('/jobs', method='POST', body=json.dumps(body), headers={'X-Test-Auth': 'owner'})
        self.assertEqual(response.code, 400)
        with self.sessions() as session:
            self.assertEqual(session.query(Job).count(), 0)

    def test_compiled_legacy_job_actions_cannot_mutate_bk_jobs(self):
        methods = [(service.PlatformSpecificDeviceJobHandler, 'post'),
                   (service.PlatformSpecificJobScriptVersionHandler, 'post'),
                   (service.PlatformSpecificJobTaskListHandler, 'delete')]
        originals = [(cls, method, getattr(cls, method)) for cls, method in methods]
        class Refusal(Exception): pass
        class FakeHandler:
            def __init__(self, mode): self.mode = mode
            def get_login_user(self, session=None): return SimpleNamespace(id=1)
            def get_login_user_job_owner(self, job_id, user=None, session=None):
                return user, SimpleNamespace(mode=self.mode)
            def throw(self, *args): raise Refusal()
        try:
            for cls, method in methods:
                setattr(cls, method, lambda self, job_id: 'native-called')
            bk_fleet.install_legacy_action_guards()
            bk_fleet.install_legacy_action_guards()
            for cls, method in methods:
                with self.subTest(method=method, cls=cls.__name__):
                    self.assertEqual(getattr(cls, method)(FakeHandler('loop'), 1), 'native-called')
                    with self.assertRaises(Refusal):
                        getattr(cls, method)(FakeHandler('bk_managed'), 1)
        finally:
            for cls, method, original in originals:
                setattr(cls, method, original)


if __name__ == '__main__':
    unittest.main()
