import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_onboard', ROOT / 'server/bk_native_onboard.py')
onboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(onboard)


class OnboardingTests(unittest.TestCase):
    def reply(self, body=None, exitstatus=0):
        return SimpleNamespace(exitstatus=exitstatus, stdout=json.dumps(body or {}).encode())

    def test_success_requires_confirmed_live_guard(self):
        device = Mock()
        device.execute_script.side_effect = [self.reply(), self.reply({'state': 'connected'}),
            self.reply({'installed': True, 'restart_required': False}),
            self.reply({'installed': True, 'restart_required': False}), self.reply()]
        result = onboard.provision(device, ROOT / 'ops', request_id='a' * 32)
        self.assertEqual({'state': 'connected'}, result)
        self.assertEqual(4, device.upload_file.call_count)
        self.assertIn('--confirm', device.execute_script.call_args_list[3].args[0])

    def test_install_reports_reload_until_guard_is_confirmed(self):
        device = Mock()
        device.execute_script.side_effect = [self.reply(), self.reply({'state': 'connected'}),
            self.reply({'installed': True, 'restart_required': True}), self.reply(exitstatus=1), self.reply()]
        self.assertEqual({'state': 'reload_required'}, onboard.provision(device, ROOT / 'ops', request_id='b' * 32))

    def test_missing_app_never_installs_guard(self):
        device = Mock()
        device.execute_script.side_effect = [self.reply(), self.reply({'state': 'not_installed'}), self.reply()]
        self.assertEqual({'state': 'not_installed'}, onboard.provision(device, ROOT / 'ops', request_id='c' * 32))
        self.assertEqual(3, device.execute_script.call_count)

    def test_unchanged_files_do_not_imply_guard_loaded(self):
        device = Mock()
        device.execute_script.side_effect = [self.reply(), self.reply({'state': 'connected'}),
            self.reply({'installed': True, 'restart_required': False}),
            self.reply(exitstatus=1), self.reply()]
        self.assertEqual({'state': 'reload_required'}, onboard.provision(device, ROOT / 'ops', request_id='e' * 32))

    def test_unknown_transfer_does_not_replay_or_delete_stage(self):
        device = Mock()
        device.execute_script.return_value = self.reply()
        device.upload_file.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            onboard.provision(device, ROOT / 'ops', request_id='d' * 32)
        self.assertEqual(1, device.execute_script.call_count)

    def test_stage_path_is_never_user_controlled(self):
        device = Mock()
        with self.assertRaisesRegex(onboard.OnboardError, 'invalid_request'):
            onboard.provision(device, ROOT / 'ops', request_id='../../outside')
        device.execute_script.assert_not_called()
