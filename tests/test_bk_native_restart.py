from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from server import bk_native_restart as restart


class RestartDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bk-restart-bundle-')
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name)
        for name in restart.FILES: (self.bundle / name).write_text('source ' + name)
        self.device = Mock()
        self.attempt = 'a' * 32

    def reply(self, status=0, output=b'{}'):
        return SimpleNamespace(exitstatus=status, stdout=output)

    def test_prepare_validates_source_and_dispatch_uses_one_receipt(self):
        self.device.execute_script.side_effect = [self.reply(), self.reply(output=b'{"state":"ready"}'),
            self.reply(output=('{"state":"prepared","attempt":"' + self.attempt + '"}').encode())]
        command=restart.prepare(self.device,self.bundle,self.attempt)
        self.assertEqual(2,self.device.upload_file.call_count)
        self.assertTrue(restart.dispatch(self.device,command,self.attempt))
        self.assertEqual(3,self.device.execute_script.call_count)

    def test_failed_check_cannot_dispatch(self):
        self.device.execute_script.side_effect=[self.reply(),self.reply(status=1)]
        with self.assertRaisesRegex(ValueError,'restart check failed'):
            restart.prepare(self.device,self.bundle,self.attempt)
        self.assertEqual(2,self.device.execute_script.call_count)

    def test_wrong_or_unknown_receipt_is_not_success(self):
        self.device.execute_script.return_value=self.reply(output=b'{"state":"prepared","attempt":"other"}')
        self.assertFalse(restart.dispatch(self.device,'command',self.attempt))
        self.device.execute_script.assert_called_once()

if __name__ == '__main__': unittest.main()
