import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from ops.bk_forward_runtime import configure

class ForwardRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bk-forward-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);base=self.root/'phone-pilot/bk-forwarding';base.mkdir(parents=True)
        data=b'# owned\n';(base/'bk_fwd_service.py').write_bytes(data)
        (base/'install.json').write_text(json.dumps({'installed':True,'files':{'bk_fwd_service.py':hashlib.sha256(data).hexdigest()},'command':'sidecar','previous_command':'native'}))
        self.path=self.root/'.local/run/services/fwd';self.path.parent.mkdir(parents=True)
        self.path.write_text('[program:fwd]\ncommand=native\nenvironment=KEEP=yes\n')
        self.s=Mock();self.s.reloadConfig.return_value=[([],['fwd'],[])];self.s.getProcessInfo.return_value={'statename':'RUNNING'}

    def test_only_owned_program_is_replaced_and_can_be_restored(self):
        configure(self.s,self.root)
        self.assertIn('command = sidecar',self.path.read_text());self.assertIn('KEEP=yes',self.path.read_text())
        self.s.stopProcess.assert_called_once_with('fwd',True);self.s.addProcessGroup.assert_called_once_with('fwd')
        configure(self.s,self.root,restore=True);self.assertIn('command = native',self.path.read_text())

    def test_other_pending_service_change_leaves_all_processes_alone(self):
        before=self.path.read_bytes();self.s.reloadConfig.return_value=[([],['fwd','uiautomator'],[])]
        with self.assertRaisesRegex(RuntimeError,'Other service'):configure(self.s,self.root)
        self.assertEqual(self.path.read_bytes(),before);self.s.stopProcess.assert_not_called()

    def test_unknown_command_or_modified_file_is_not_overwritten(self):
        self.path.write_text('[program:fwd]\ncommand=another\n');before=self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'Unowned'):configure(self.s,self.root)
        self.assertEqual(self.path.read_bytes(),before)
        (self.root/'phone-pilot/bk-forwarding/bk_fwd_service.py').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError,'Changed forwarding'):configure(self.s,self.root)
        self.s.reloadConfig.assert_not_called()
