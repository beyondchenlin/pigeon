import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
spec = importlib.util.spec_from_file_location('native_restart_once', ROOT / 'ops/bk_native_restart_once.py')
restart = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restart)


class NativeRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bk-native-restart-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user = self.root / 'user'
        self.guard = self.user / 'phone-pilot/bk-ai'
        self.guard.mkdir(parents=True)
        (self.guard / 'lock').touch()
        self.receipts = self.user / 'phone-pilot/guard-restart'
        self.receipts.mkdir()
        self.attempt = 'a' * 32
        self.receipt = self.receipts / (self.attempt + '.json')
        self.receipt.write_text('{}')
        self.runtime = self.root / 'runtime'
        (self.runtime / 'bin').mkdir(parents=True)
        (self.runtime / 'bin/launch.sh').write_text('# fake')

    def test_guard_requires_official_pid_identity_and_no_active_lease(self):
        pid = 12345
        proc = self.root / 'proc'
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / 'stat').write_text('12345 (test) ' + ' '.join(['x'] * 19 + ['42']))
        (self.user / 'lamda.pid').write_text(str(pid))
        state={'guard_pid':pid,'guard_identity':'42','guard_protocol':{'version':2,'pid':pid,'identity':'42'},'ai':None,'manual':[]}
        (self.guard / 'state.json').write_text(json.dumps(state))
        self.assertEqual((pid,'42'), restart.inspect_guard(self.user,proc,uid=os.getuid()))
        state['ai']='active'; (self.guard / 'state.json').write_text(json.dumps(state))
        with self.assertRaises(ValueError): restart.inspect_guard(self.user,proc,uid=os.getuid())
        state['ai']=None; (self.guard / 'state.json').write_text(json.dumps(state))
        (self.user / 'lamda.pid').write_text('9999')
        with self.assertRaises(ValueError): restart.inspect_guard(self.user,proc,uid=os.getuid())

    def test_one_signal_and_one_launch_with_no_replay(self):
        child=Mock(); child.wait.return_value=0
        with patch.object(restart,'inspect_guard',return_value=(12345,'42')), \
             patch.object(restart,'identity',side_effect=['42',None,None]), \
             patch.object(restart.os,'kill') as kill, patch.object(restart.time,'sleep'), \
             patch.object(restart,'launch_official',return_value=child) as launch:
            restart.worker(self.user,self.runtime,self.attempt,12345,'42')
            restart.worker(self.user,self.runtime,self.attempt,12345,'42')
        kill.assert_called_once_with(12345,restart.signal.SIGUSR2)
        launch.assert_called_once()
        self.assertEqual('launched',json.loads(self.receipt.read_text())['state'])

    def test_mismatched_guard_never_signals_or_launches(self):
        with patch.object(restart,'inspect_guard',return_value=(12345,'43')), \
             patch.object(restart.os,'kill') as kill, patch.object(restart.time,'sleep'), \
             patch.object(restart,'launch_official') as launch:
            restart.worker(self.user,self.runtime,self.attempt,12345,'42')
        kill.assert_not_called(); launch.assert_not_called()
        self.assertEqual('failed',json.loads(self.receipt.read_text())['state'])

    def test_unknown_process_identity_never_launches_another_service(self):
        with patch.object(restart,'inspect_guard',return_value=(12345,'42')), \
             patch.object(restart,'identity',side_effect=ValueError('unknown')), \
             patch.object(restart.os,'kill') as kill, patch.object(restart.time,'sleep'), \
             patch.object(restart,'launch_official') as launch:
            restart.worker(self.user,self.runtime,self.attempt,12345,'42')
        kill.assert_called_once(); launch.assert_not_called()
        self.assertEqual('unknown',json.loads(self.receipt.read_text())['state'])

if __name__ == '__main__': unittest.main()
