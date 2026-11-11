"""Admission diagnostics stay useful without exposing arbitrary phone responses."""
import unittest
from server.bk_ai_transport import refusal_code


class RefusalDiagnosticTests(unittest.TestCase):
    def test_distinguishes_control_readiness_and_access(self):
        for reason in ('control_busy_or_guard_unavailable', 'manual_takeover',
                       'device_not_ready', 'phone_access_disabled', 'conflict'):
            with self.subTest(reason=reason):
                result = refusal_code({'http': 409, 'body': {'error': {'code': reason}}})
                self.assertEqual(result, 'phone_refused_409:' + reason)
                self.assertLessEqual(len(result), 64)

    def test_unknown_reason_and_message_are_never_persisted(self):
        for body in (None, [], {}, {'error': 'private'}, {'error': {'code': []}},
                     {'error': {'code': 'secret-token', 'message': 'private details'}},
                     {'error': {'code': 'device_not_ready', 'message': 'private details'}}):
            with self.subTest(body=body):
                result = refusal_code({'http': 409, 'body': body})
                self.assertNotIn('private', result)
                self.assertNotIn('secret', result)
                self.assertIn(result, ('phone_refused_409', 'phone_refused_409:device_not_ready'))
