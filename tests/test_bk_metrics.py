"""Regression tests for missing/zero battery readings and Android units."""
import unittest
from server.bk_metrics import parse_metrics


class MetricsTests(unittest.TestCase):
    def test_real_android_sample(self):
        result = parse_metrics('''__BK_BATTERY__
  AC powered: false
  USB powered: false
  level: 49
  scale: 100
  temperature: 267
  voltage: 3817
  status: 3
__BK_STORAGE__
Filesystem 1K-blocks Used Available Use% Mounted on
/dev/block/dm-48 233544460 69735568 163677820 30% /data
__BK_DISPLAY__
Physical size: 1080x2388
Physical density: 480
__BK_SYSTEM__
vivo
V2220A
14
34
arm64-v8a
''')
        self.assertEqual(result['battery']['percent'], 49)
        self.assertFalse(result['battery']['charging'])
        self.assertEqual(result['battery']['temperature_c'], 26.7)
        self.assertEqual(result['battery']['voltage_v'], 3.817)
        self.assertEqual(result['storage']['total'], 233544460 * 1024)
        self.assertEqual(result['system']['model'], 'V2220A')
        self.assertEqual(result['display']['height'], 2388)

    def test_zero_is_not_full(self):
        self.assertEqual(parse_metrics('__BK_BATTERY__\nlevel: 0\nscale: 100')['battery']['percent'], 0)

    def test_battery_service_header_does_not_consume_ac_source(self):
        result = parse_metrics('''__BK_BATTERY__
Current Battery Service state:
  AC powered: true
  USB powered: false
  Wireless powered: false
  Dock powered: false
  status: 2
  level: 73
  scale: 100
''')
        self.assertEqual(result['battery']['source'], 'AC')
        self.assertTrue(result['battery']['charging'])
        self.assertEqual(result['battery']['percent'], 73)

    def test_empty_field_does_not_consume_next_line(self):
        result = parse_metrics('__BK_BATTERY__\nlevel: 0\nscale: 100\nvoltage:\n  temperature: 267\n')
        self.assertIsNone(result['battery']['voltage_v'])
        self.assertEqual(result['battery']['temperature_c'], 26.7)

    def test_sdk_returns_bytes(self):
        self.assertEqual(parse_metrics(b'__BK_BATTERY__\nlevel: 49\nscale: 100')['battery']['percent'], 49)

    def test_missing_is_unknown(self):
        result = parse_metrics('permission denied')
        self.assertIsNone(result['battery']['percent'])
        self.assertIsNone(result['battery']['charging'])
        self.assertEqual(result['storage'], {})

    def test_bad_values_are_unknown(self):
        for pair in [('NaN', '100'), ('inf', '100'), ('-1', '100'), ('200', '100'), ('10', '0'), ('10', '-10')]:
            with self.subTest(pair=pair):
                self.assertIsNone(parse_metrics('__BK_BATTERY__\nlevel: %s\nscale: %s' % pair)['battery']['percent'])

    def test_override_and_non_100_scale(self):
        result = parse_metrics('__BK_BATTERY__\nlevel: 1\nscale: 2\nUSB powered: true\n__BK_DISPLAY__\nPhysical size: 1080x2388\nOverride size: 720x1600\nPhysical density: 480\nOverride density: 320')
        self.assertEqual(result['battery']['percent'], 50)
        self.assertEqual(result['battery']['source'], 'USB')
        self.assertEqual(result['display']['width'], 720)
        self.assertEqual(result['display']['density'], 320)


if __name__ == '__main__':
    unittest.main()
