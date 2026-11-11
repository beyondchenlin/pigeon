import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('bk_adb_keepalive', Path(__file__).resolve().parents[1] / 'ops/bk_adb_keepalive.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MaintenanceTests(unittest.TestCase):
    def test_stale_offline_connection_is_replaced_and_checked(self):
        state = 'offline'
        calls = []
        def adb(command):
            nonlocal state
            calls.append(command)
            if command == 'host:devices':
                # An unrelated device must never be selected for recovery.
                return 'other-phone\tdevice\n' + (module.SERIAL+'\t'+state+'\n' if state else '')
            if command == 'host:disconnect:'+module.SERIAL:
                state = ''
                return 'disconnected'
            if command == 'host:connect:'+module.SERIAL:
                if state == 'offline':
                    return 'already connected to '+module.SERIAL
                state = 'device'
                return 'connected to '+module.SERIAL
            self.fail(command)
        self.assertEqual(module.maintain(adb), 'device')
        self.assertEqual(calls.count('host:disconnect:'+module.SERIAL), 1)

    def test_healthy_and_unauthorized_connections_are_untouched(self):
        for state in ('device', 'unauthorized'):
            calls = []
            def adb(command):
                calls.append(command)
                return module.SERIAL+'\t'+state+'\n'
            self.assertEqual(module.maintain(adb), state)
            self.assertEqual(calls, ['host:devices'])

    def test_success_text_without_online_device_is_not_success(self):
        def adb(command):
            return 'already connected to '+module.SERIAL if command.startswith('host:connect:') else ''
        self.assertEqual(module.maintain(adb), 'missing')


if __name__ == '__main__':
    unittest.main()
