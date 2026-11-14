import unittest
from server.bk_job_timing import deadline_reached, deadline_timestamp


class DeadlineTests(unittest.TestCase):
    def test_timestamp_runs_until_deadline(self):
        self.assertFalse(deadline_reached(1800000100, 1800000000, 1800000099))
        self.assertTrue(deadline_reached(1800000100, 1800000000, 1800000100))

    def test_legacy_duration_is_relative_to_original_start(self):
        self.assertEqual(deadline_timestamp(120, 1800000000), 1800000120)
        self.assertFalse(deadline_reached(120, 1800000000, 1800000119))
        self.assertTrue(deadline_reached(120, 1800000000, 1800000120))

    def test_invalid_deadline_fails_closed(self):
        self.assertTrue(deadline_reached(0, 1800000000, 1800000000))
        self.assertTrue(deadline_reached(120, 0, 1800000000))
        self.assertTrue(deadline_reached(None, 1800000000, 1800000000))


if __name__ == '__main__':
    unittest.main()
