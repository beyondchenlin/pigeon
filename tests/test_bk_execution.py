"""Pure contract validation; no device, credentials or model calls."""
import copy
import unittest
from server.bk_execution import (ContractError, validate_execution, supervision_timeout,
                                 request_signature, supports_execution)


def contract():
    return {'version':1, 'kind':'browse', 'duration_s':7200, 'clock':'elapsed',
            'finish_timeout_s':60, 'stop_timeout_s':30, 'progress_interval_s':600,
            'event_latency_s':30, 'budget':{'max_tokens':12000},
            'progress_requirements':{'min_observations':2,'min_active_s':3600,'max_stall_s':120}}


class ExecutionContractTests(unittest.TestCase):
    def test_two_hours_has_separate_start_finish_event_and_stop_allowance(self):
        value = validate_execution(contract())
        self.assertEqual(supervision_timeout(value), 7440)
        self.assertEqual(supervision_timeout(None), 900)
        with self.assertRaises(ContractError):
            supervision_timeout(value, 7200, True)

    def test_unknown_version_clock_kind_fields_or_null_do_not_silently_downgrade(self):
        values = [None, {}, dict(contract(), version=2), dict(contract(), clock='active'),
                  dict(contract(), kind='publish'), dict(contract(), extra=True)]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_execution(value)

    def test_strict_numeric_types_reject_bool_float_and_missing_budget(self):
        for value in (True, 7200.0, '7200', 0, 10001):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_execution(dict(contract(), duration_s=value))
        for value in ({}, {'max_tokens':None}, {'max_tokens':10000001}, {'max_tokens':True},
                      {'max_tokens':1, 'price':0}):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_execution(dict(contract(), budget=value))

    def test_progress_requires_observation_and_active_time_and_bounded_stall(self):
        for key, value in [('min_observations',1), ('min_active_s',0),
                           ('min_active_s',7201), ('max_stall_s',0)]:
            changed = contract()
            changed['progress_requirements'][key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_execution(changed)

    def test_contract_is_copied_and_key_order_does_not_change_request_identity(self):
        value = contract()
        validated = validate_execution(value)
        value['budget']['max_tokens'] = 1
        self.assertEqual(validated['budget']['max_tokens'], 12000)
        self.assertEqual(request_signature('goal',7440,validated),
                         request_signature('goal',7440,dict(reversed(list(validated.items())))))
        changed = copy.deepcopy(validated)
        changed['budget']['max_tokens'] -= 1
        self.assertNotEqual(request_signature('goal',7440,validated), request_signature('goal',7440,changed))

    def test_capability_gate_rejects_truthy_or_incomplete_claims(self):
        body = {'execution_versions':[1], 'task_wait_revision':True, 'terminal_evidence':True}
        self.assertTrue(supports_execution({'http':200,'body':body}))
        for field, value in [('execution_versions',[True]),('task_wait_revision',1),('terminal_evidence',False)]:
            self.assertFalse(supports_execution({'http':200,'body':dict(body, **{field:value})}))
        self.assertFalse(supports_execution({'http':404,'body':body}))


if __name__ == '__main__':
    unittest.main()
