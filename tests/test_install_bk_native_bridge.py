import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

OPS = Path(__file__).resolve().parents[1] / 'ops'
spec = importlib.util.spec_from_file_location('native_installer', OPS / 'install_bk_native_bridge.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@unittest.skipIf(sys.platform == 'win32', 'Android installer uses POSIX file locking')
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bk-native-installer-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / 'runtime'
        self.user = self.root / 'user'
        bundle = self.runtime / 'lib/python3.12/site-packages/lamda/web/bundle.cpython-312.so'
        bundle.parent.mkdir(parents=True)
        bundle.write_bytes(b'test-only-runtime')
        self.hash_patch = patch.object(installer, 'BUNDLE_HASH', installer.digest(bundle.read_bytes()))
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)

    def run_install(self):
        return installer.install(self.runtime, self.user, OPS, boundary=self.root, uid=2000)

    def test_install_is_repeatable_and_preserves_existing_files(self):
        self.assertTrue(self.run_install()['changed'])
        second = self.run_install()
        self.assertFalse(second['changed'])
        self.assertTrue(second['restart_required'])
        self.assertTrue((self.user / 'modules/extension/bk_ai_guard_extension.py').is_file())
        manifest = json.loads((self.user / 'phone-pilot/bk-native-bridge-install.json').read_text())
        self.assertEqual(4, len(manifest['files']))

    def test_confirmation_requires_new_live_guard_and_unchanged_files(self):
        self.run_install()
        with self.assertRaisesRegex(RuntimeError, 'not yet loaded'):
            installer.confirm_activation(self.user, OPS, boundary=self.root, uid=2000)
        spec = importlib.util.spec_from_file_location('phone_test', OPS / 'bk_ai_phone.py')
        phone = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(phone)
        root = self.user / 'phone-pilot/bk-ai'
        with phone.state(root) as data:
            pid = os.getpid()
            identity = phone.process_identity(pid)
            data.update(guard_pid=pid, guard_identity=identity,
                        guard_protocol={'version': 2, 'pid': pid, 'identity': identity})
        self.assertTrue(phone.live_guard_ready(root))
        self.assertFalse(phone.native_guard_ready(root), 'updated files must not use the old guard')
        self.assertFalse(installer.confirm_activation(self.user, OPS, boundary=self.root, uid=2000)['restart_required'])
        self.assertTrue(phone.native_guard_ready(root))
        (self.user / 'modules/bk_native_mcp.py').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'file changed'):
            installer.confirm_activation(self.user, OPS, boundary=self.root, uid=2000)

    def test_unknown_file_refused_without_overwriting(self):
        target = self.user / 'modules/bk_ai_phone.py'
        target.parent.mkdir(parents=True)
        target.write_text('other work')
        with self.assertRaisesRegex(RuntimeError, 'not owned'):
            self.run_install()
        self.assertEqual('other work', target.read_text())
        self.assertFalse((self.user / 'modules/bk_native_mcp.py').exists())

    def test_busy_phone_cannot_be_reconfigured(self):
        state = self.user / 'phone-pilot/bk-ai'
        state.mkdir(parents=True)
        (state / 'state.json').write_text(json.dumps({'ai': 'in-flight', 'manual': []}))
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            self.run_install()
        self.assertFalse((self.user / 'modules/bk_ai_phone.py').exists())

    def test_unverified_runtime_and_outside_boundary_are_rejected(self):
        with patch.object(installer, 'BUNDLE_HASH', 'wrong'):
            with self.assertRaisesRegex(RuntimeError, 'unverified'):
                self.run_install()
        with self.assertRaisesRegex(RuntimeError, 'outside'):
            installer.install(self.runtime, self.root.parent, OPS, boundary=self.root, uid=2000)

    def test_partial_failure_does_not_leave_a_partial_install(self):
        original = installer.atomic_write
        count = 0
        def fail_once(path, data):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError('simulated write failure')
            return original(path, data)
        with patch.object(installer, 'atomic_write', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'simulated'):
                self.run_install()
        for relative in ('bk_ai_phone.py', 'bk_ai_phone_mcp_candidate.py',
                         'bk_native_mcp.py', 'extension/bk_ai_guard_extension.py'):
            self.assertFalse((self.user / 'modules' / relative).exists())
        self.assertFalse((self.user / 'phone-pilot/bk-native-bridge-install.json').exists())
