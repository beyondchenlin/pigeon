import unittest
from datetime import datetime, timezone

from server.bk_plan import (PlanError, execution_id, next_daily, parse_schedule,
                            plan_fingerprint, resolve_target, validate_plan)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.devices = [
            {'domain': 'a1', 'name': '手机一', 'group_id': 4},
            {'domain': 'a2', 'name': '手机二', 'group_id': 4},
            {'domain': 'b1', 'name': '手机一', 'group_id': 7},
        ]
        self.groups = [{'id': 4, 'name': '美国组'}, {'id': 7, 'name': '其他组'}]

    def test_group_is_resolved_to_a_snapshot(self):
        chosen = resolve_target({'kind': 'group', 'name': '美国组'}, self.devices, self.groups)
        self.devices.append({'domain': 'a3', 'name': '新手机', 'group_id': 4})
        self.assertEqual(chosen, ['a1', 'a2'])

    def test_ambiguous_name_does_not_pick_first(self):
        with self.assertRaisesRegex(PlanError, 'target_ambiguous'):
            resolve_target({'kind': 'device', 'name': '手机一'}, self.devices, self.groups)
        self.assertEqual(resolve_target({'kind': 'device', 'name': 'a1'}, self.devices, self.groups), ['a1'])

    def test_unknown_or_empty_group_cannot_dispatch(self):
        for name, expected in [('不存在', 'target_not_found'), ('空组', 'group_empty')]:
            with self.subTest(name=name), self.assertRaisesRegex(PlanError, expected):
                resolve_target({'kind': 'group', 'name': name}, self.devices, self.groups + [{'id': 9, 'name': '空组'}])

    def test_daily_schedule_preserves_explicit_timezone(self):
        schedule = {'kind': 'daily', 'at': '20:00', 'timezone': 'Asia/Shanghai'}
        self.assertEqual(next_daily(schedule, datetime(2026, 11, 14, 11, 0, tzinfo=timezone.utc)),
                         datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(next_daily(schedule, datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)),
                         datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc))

    def test_dst_gap_and_fold_do_not_guess(self):
        for at in ('2026-03-08T02:30', '2026-11-01T01:30'):
            with self.subTest(at=at), self.assertRaisesRegex(PlanError, 'schedule_ambiguous_time'):
                parse_schedule({'kind': 'once', 'at': at, 'timezone': 'America/New_York'})

    def test_schedule_rejects_missing_timezone_and_extra_fields(self):
        for schedule in ({'kind': 'daily', 'at': '20:00'},
                         {'kind': 'now', 'unexpected': True},
                         {'kind': 'daily', 'at': '8:00', 'timezone': 'Asia/Shanghai'}):
            with self.subTest(schedule=schedule), self.assertRaises(PlanError):
                parse_schedule(schedule)

    def test_plan_requires_exact_fields_and_preserves_user_choices(self):
        candidate = {'goal': ' 浏览 ', 'target': {'kind': 'group', 'name': '美国组'},
                     'schedule': {'kind': 'daily', 'at': '20:00', 'timezone': 'Asia/Shanghai'}}
        plan = validate_plan(candidate, self.devices, self.groups)
        self.assertEqual(plan['goal'], '浏览')
        self.assertEqual(plan['devices'], ['a1', 'a2'])
        self.assertEqual((plan['membership'], plan['missed']), ('snapshot', 'skip'))
        self.assertEqual(plan_fingerprint(plan), plan_fingerprint(dict(reversed(list(plan.items())))))
        with self.assertRaisesRegex(PlanError, 'plan_invalid'):
            validate_plan(dict(candidate, code='arbitrary'), self.devices, self.groups)

    def test_each_occurrence_and_device_has_stable_distinct_identity(self):
        original = execution_id(5, 'a1', '2026-11-14T12:00:00Z')
        self.assertEqual(original, execution_id(5, 'a1', '2026-11-14T12:00:00Z'))
        self.assertNotEqual(original, execution_id(5, 'a2', '2026-11-14T12:00:00Z'))
        self.assertNotEqual(original, execution_id(5, 'a1', '2026-11-15T12:00:00Z'))

    def test_browse_requires_complete_execution_contract(self):
        candidate = {'goal':'浏览', 'target':{'kind':'device', 'name':'a1'}, 'schedule':{'kind':'now'},
                     'execution':{'duration_s':7200}}
        with self.assertRaisesRegex(PlanError, 'execution_invalid'):
            validate_plan(candidate, self.devices, self.groups)


if __name__ == '__main__':
    unittest.main()
