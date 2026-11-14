"""Atomic job creation with frozen, authorized device bindings."""
import os
import unittest
import uuid
from datetime import datetime, timezone

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from executor.models import Device, DeviceJob, Group, Job, Task, User, UserDevice
from server.bk_ai_store import AiTask
from server.bk_native_reload import NativeReload
from server.bk_fleet_store import (BK_RUNNING, BK_STOPPED, cancel_job, create_job,
                                   materialize_due, sync_job_results)
from server.bk_plan import PlanError, validate_plan


class FleetStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        for table in (User.__table__, Device.__table__, Group.__table__, UserDevice.__table__,
                      Job.__table__, DeviceJob.__table__, Task.__table__, AiTask.__table__, NativeReload.__table__):
            table.create(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as session:
            session.add(User(id=1, name='owner'))
            session.add(Group(id=1, owner_id=1, name='我的组'))
            session.add_all([Device(id=1, domain='a1', state='online'), Device(id=2, domain='a2', state='offline'),
                             Device(id=3, domain='notowned')])
            session.add_all([UserDevice(user_id=1, device_id=1), UserDevice(user_id=1, device_id=2)])
        self.plan = validate_plan({'goal': '读取状态', 'target': {'kind': 'group', 'name': '我的组'},
                                   'schedule': {'kind': 'now'}},
            [{'domain': 'a1', 'group_id': 1}, {'domain': 'a2', 'group_id': 1}], [{'id': 1, 'name': '我的组'}])

    def tearDown(self):
        self.engine.dispose()

    def test_retry_returns_same_parent_and_same_frozen_bindings(self):
        request = str(uuid.uuid4())
        with self.Session.begin() as session:
            job, created = create_job(session, 1, request, self.plan, now=1800000000)
            job_id = job.id
            self.assertTrue(created)
            self.assertEqual(job.state, BK_RUNNING)
            self.assertEqual(job.group_id, 1)
        with self.Session.begin() as session:
            job, created = create_job(session, 1, request, self.plan, now=1800000100)
            self.assertFalse(created)
            self.assertEqual(job.id, job_id)
            self.assertEqual(sorted(row.device_id for row in session.query(DeviceJob).all()), [1, 2])
            self.assertEqual(session.query(Job).count(), 1)

    def test_same_request_with_different_goal_is_rejected(self):
        request = str(uuid.uuid4())
        with self.Session.begin() as session:
            create_job(session, 1, request, self.plan, now=1800000000)
        with self.Session.begin() as session, self.assertRaisesRegex(PlanError, 'request_reused'):
            create_job(session, 1, request, dict(self.plan, goal='another'), now=1800000000)

    def test_device_removed_after_preview_blocks_entire_plan(self):
        with self.Session.begin() as session:
            session.query(UserDevice).filter_by(device_id=2).delete()
        with self.Session.begin() as session, self.assertRaisesRegex(PlanError, 'device_access_changed'):
            create_job(session, 1, str(uuid.uuid4()), self.plan, now=1800000000)
        with self.Session() as session:
            self.assertEqual(session.query(Job).count(), 0)
            self.assertEqual(session.query(DeviceJob).count(), 0)

    def test_due_round_has_one_receipt_per_device_and_skips_offline(self):
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), self.plan, now=1800000000)
            job_id = job.id
        with self.Session.begin() as session:
            dispatched = materialize_due(session, job_id, now=1800000001)
            self.assertEqual(len(dispatched), 1)
        with self.Session.begin() as session:
            self.assertEqual(materialize_due(session, job_id, now=1800000002), [])
            rows = {row.device_id: row for row in session.query(Task).all()}
            self.assertEqual(rows[1].state, 'bk_pending')
            self.assertEqual(rows[2].reason, 'device_offline')
            self.assertEqual(session.query(AiTask).count(), 1)
            self.assertEqual(rows[1].task_id, dispatched[0])

    def test_missed_round_does_not_dispatch_after_restart(self):
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), self.plan, now=1800000000)
            job_id = job.id
        with self.Session.begin() as session:
            self.assertEqual(materialize_due(session, job_id, now=1800000060), [])
            self.assertEqual({row.reason for row in session.query(Task)}, {'missed_schedule'})
            self.assertEqual(session.query(AiTask).count(), 0)

    def test_scheduled_round_does_not_dispatch_during_reload(self):
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), self.plan, now=1800000000)
            job_id = job.id
            session.add(NativeReload(device_id=1, user_id=1, attempt_id=str(uuid.uuid4()), phase='reserved'))
        with self.Session.begin() as session:
            self.assertEqual(materialize_due(session, job_id, now=1800000001), [])
            self.assertEqual(session.query(Task).filter_by(device_id=1).one().reason, 'device_busy')
            self.assertEqual(session.query(AiTask).count(), 0)

    def test_stopped_parent_is_a_dispatch_barrier(self):
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), self.plan, now=1800000000)
            job_id = job.id
            job.state = BK_STOPPED
        with self.Session.begin() as session:
            self.assertEqual(materialize_due(session, job_id, now=1800000001), [])
            self.assertEqual(session.query(Task).count(), 0)

    def test_cancel_future_preserves_current_but_stop_current_records_intent(self):
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), self.plan, now=1800000000)
            job_id = job.id
        with self.Session.begin() as session:
            task_id = materialize_due(session, job_id, now=1800000001)[0]
        with self.Session.begin() as session:
            self.assertEqual(cancel_job(session, job_id, 1), [])
            self.assertFalse(session.get(AiTask, task_id).cancel_requested)
        with self.Session.begin() as session:
            self.assertEqual(cancel_job(session, job_id, 1, stop_current=True), [task_id])
            self.assertTrue(session.get(AiTask, task_id).cancel_requested)
        with self.Session.begin() as session:
            self.assertEqual(materialize_due(session, job_id, now=1800000002), [])

    def test_unknown_receipt_remains_busy_until_phone_confirms_terminal(self):
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), self.plan, now=1800000000)
            job_id = job.id
        with self.Session.begin() as session:
            task_id = materialize_due(session, job_id, now=1800000001)[0]
            session.get(AiTask, task_id).status = 'unknown'
            sync_job_results(session, job_id, now=1800000002)
            self.assertEqual(session.query(Task).filter_by(task_id=task_id).one().state, 'bk_active')
        with self.Session.begin() as session:
            session.get(AiTask, task_id).status = 'done'
            sync_job_results(session, job_id, now=1800000003)
            self.assertEqual(session.query(Task).filter_by(task_id=task_id).one().state, 'success')
            self.assertEqual(session.get(Job, job_id).success, 1)

    def test_daily_rounds_keep_snapshot_and_separate_receipts(self):
        daily = dict(self.plan, schedule={'kind':'daily', 'at':'20:00', 'timezone':'Asia/Shanghai'})
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), daily, now=1794646800)
            job_id, first_due = job.id, job.config['bk_fleet']['next_at']
        with self.Session.begin() as session:
            ids = materialize_due(session, job_id, now=first_due)
            first = ids[0]
            session.get(AiTask, first).status = 'done'
            sync_job_results(session, job_id, now=first_due + 2)
            second_due = session.get(Job, job_id).config['bk_fleet']['next_at']
            # Change only group membership, keeping device ownership intact.
            session.query(UserDevice).filter_by(user_id=1, device_id=1).one().group_id = 99
        with self.Session.begin() as session:
            ids = materialize_due(session, job_id, now=second_due)
            self.assertEqual(len(ids), 1)
            self.assertNotEqual(ids[0], first)
            self.assertEqual(session.query(Task).count(), 4)

    def test_browse_contract_is_preserved_for_each_phone(self):
        execution = {'version':1, 'kind':'browse', 'duration_s':7200, 'clock':'elapsed',
            'finish_timeout_s':60, 'stop_timeout_s':30, 'progress_interval_s':600, 'event_latency_s':30,
            'budget':{'max_tokens':50000}, 'progress_requirements':{'min_observations':2,
                'min_active_s':60, 'max_stall_s':600}}
        plan = dict(self.plan, execution=execution)
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), plan, now=1800000000)
            job_id = job.id
        with self.Session.begin() as session:
            task_id = materialize_due(session, job_id, now=1800000001)[0]
            receipt = session.get(AiTask, task_id)
            self.assertEqual(receipt.execution, execution)
            self.assertEqual(receipt.deadline - receipt.created, 7440)

    def test_dst_gap_pauses_future_round_without_losing_current_round(self):
        now = datetime(2026, 3, 7, 7, 0, tzinfo=timezone.utc).timestamp()
        plan = dict(self.plan, schedule={'kind':'daily', 'at':'02:30', 'timezone':'America/New_York'})
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), plan, now=now)
            job_id, due = job.id, job.config['bk_fleet']['next_at']
        with self.Session.begin() as session:
            self.assertEqual(len(materialize_due(session, job_id, now=due)), 1)
            job = session.get(Job, job_id)
            self.assertEqual(job.state, 'bk_paused')
            self.assertEqual(job.config['bk_fleet']['schedule_error'], 'schedule_ambiguous_time')
            self.assertIsNone(job.config['bk_fleet']['next_at'])

    def test_missing_binding_never_silently_dispatches_partial_group(self):
        with self.Session.begin() as session:
            job, _ = create_job(session, 1, str(uuid.uuid4()), self.plan, now=1800000000)
            job_id = job.id
        with self.Session.begin() as session:
            session.query(DeviceJob).filter_by(job_id=job_id, device_id=2).delete()
        with self.Session.begin() as session, self.assertRaisesRegex(PlanError, 'device_binding_missing'):
            materialize_due(session, job_id, now=1800000001)
        with self.Session() as session:
            self.assertEqual(session.query(AiTask).count(), 0)


if __name__ == '__main__':
    unittest.main()
