import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))


class ExtensionStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bk-extension-startup-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'web').mkdir()
        (root / 'web/bundle.cpython-312.so').write_bytes(b'fixture')
        self.lamda = types.ModuleType('lamda')
        self.lamda.__version__ = '10.9'
        extensions = types.ModuleType('lamda.extensions')
        extensions.BaseHttpExtension = type('BaseHttpExtension', (), {})
        modules = patch.dict(sys.modules, {'lamda': self.lamda, 'lamda.extensions': extensions})
        modules.start(); self.addCleanup(modules.stop)
        environ = patch.dict(os.environ, {'PKGDIR': str(root), 'ROOTDIR': '/runtime', 'USRDIR': '/user'})
        environ.start(); self.addCleanup(environ.stop)
        spec = importlib.util.spec_from_file_location('bk_lifecycle_extension_fixture', ROOT / 'ops/bk_lifecycle_extension.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.module.NATIVE_HASH = hashlib.sha256(b'fixture').hexdigest()

    def test_verified_runtime_starts_monitor(self):
        with patch.object(self.module, 'start_monitor') as start:
            self.module.BkLifecycleExtension.setup({})
            start.assert_called_once_with('/runtime', '/user')

    def test_changed_runtime_disables_recovery_without_blocking_service(self):
        for version, digest in [('11.0', self.module.NATIVE_HASH), ('10.9', 'changed')]:
            self.lamda.__version__, self.module.NATIVE_HASH = version, digest
            with patch.object(self.module, 'start_monitor') as start, self.assertLogs(level='WARNING'):
                self.module.BkLifecycleExtension.setup({})
                start.assert_not_called()

    def test_failed_monitor_launch_does_not_break_primary_startup(self):
        with patch.object(self.module, 'start_monitor', side_effect=OSError), self.assertLogs(level='WARNING'):
            self.module.BkLifecycleExtension.setup({})


if __name__ == '__main__':
    unittest.main()
