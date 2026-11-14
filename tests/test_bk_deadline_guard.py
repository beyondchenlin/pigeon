"""Exercise deadline compatibility against the compiled executor hook."""
from contextlib import contextmanager
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from executor.handlers import event
from executor.models import Device, DeviceJob, Job, Script, ScriptVersion, Task
from server import bk_ai_store


class CompiledDeadlineGuardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        for table in (Device.__table__, Script.__table__, ScriptVersion.__table__, Job.__table__, DeviceJob.__table__, Task.__table__):
            table.create(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.orig = event.cb_task_status_prepared_imp
        self.flag = getattr(event, '_bk_ai_guard', None)
        if hasattr(event, '_bk_ai_guard'):
            delattr(event, '_bk_ai_guard')

    def tearDown(self):
        event.cb_task_status_prepared_imp = self.orig
        if self.flag is None:
            if hasattr(event, '_bk_ai_guard'): delattr(event, '_bk_ai_guard')
        else:
            event._bk_ai_guard = self.flag
        self.engine.dispose()

    def test_deadline_before_due_uses_original_loop_without_changing_database(self):
        with self.Session.begin() as session:
            job = Job(id=1, mode='deadline', state='running', count=1800000100, start_time=1800000000)
            session.add(job)
            session.flush()
            def original(s, dt):
                self.assertEqual(dt.job.mode, 'loop')
                s.flush()
                self.assertEqual(s.execute(text('select mode from job where id=1')).scalar_one(), 'deadline')
                return 'issued'
            event.cb_task_status_prepared_imp = original
            with patch.object(bk_ai_store, 'locked_device', return_value=SimpleNamespace(id=1, controlling=False)), \
                 patch.object(bk_ai_store, 'active', return_value=None), patch.object(bk_ai_store.time, 'time', return_value=1800000099):
                bk_ai_store.install_script_guard()
                self.assertEqual(event.cb_task_status_prepared_imp(session, SimpleNamespace(device_id=1, job=job)), 'issued')
            self.assertEqual(job.mode, 'deadline')
        with self.Session() as session:
            self.assertEqual(session.get(Job, 1).mode, 'deadline')

    def test_due_job_stops_without_issuing_a_new_task(self):
        with self.Session.begin() as session:
            job = Job(id=1, mode='deadline', state='running', count=120, start_time=1800000000)
            session.add(job)
            session.flush()
            event.cb_task_status_prepared_imp = lambda *args: self.fail('must not dispatch after deadline')
            with patch.object(bk_ai_store, 'locked_device', return_value=SimpleNamespace(id=1, controlling=False)), \
                 patch.object(bk_ai_store, 'active', return_value=None), patch.object(bk_ai_store.time, 'time', return_value=1800000120), \
                 patch.object(event, 'stop', return_value='stopped') as stop:
                bk_ai_store.install_script_guard()
                self.assertEqual(event.cb_task_status_prepared_imp(session, SimpleNamespace(device_id=1, job=job)), 'stopped')
                stop.assert_called_once_with(session, job, revoke=False)

    def test_real_compiled_scheduler_issues_before_deadline_without_changing_mode(self):
        with self.Session.begin() as session:
            device = Device(id=1, domain='synthetic', dev_id='synthetic', state='online')
            script = Script(id=1, name='sample', entry={'method':'run'})
            version = ScriptVersion(id=1, parent=script, version='1', code='pass')
            job = Job(id=1, mode='deadline', state='running', count=1800000100,
                      start_time=1800000000, script=version, group_id=1, config={'time_limit':60}, interval=0)
            binding = DeviceJob(id=1, device=device, job=job, task_state='idle',
                                last_issue_time=0, last_state_update_time=1800000099, cooldown=0)
            session.add_all([device, script, version, job, binding])
            session.flush()
            with patch.object(bk_ai_store, 'locked_device', return_value=SimpleNamespace(id=1, controlling=False)), \
                 patch.object(bk_ai_store, 'active', return_value=None), patch.object(bk_ai_store.time, 'time', return_value=1800000099), \
                 patch.object(event, 'is_device_in_job_group', return_value=True), \
                 patch.object(event, 'lock_job'), patch.object(event, 'send_node_command') as sent:
                bk_ai_store.install_script_guard()
                self.assertTrue(event.cb_task_status_prepared_imp(session, binding))
                sent.assert_called_once()
            self.assertEqual(job.mode, 'deadline')
            self.assertEqual(session.query(Task).count(), 1)
