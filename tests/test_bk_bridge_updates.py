import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the PIGEON runtime')
from server import bk_bridge_updates as updates
from server.bk_bridge_policy import enrolled


class BridgeUpdatesTests(unittest.TestCase):
    def test_new_mqtt_session_rechecks_current_files_without_installing(self):
        worker = updates.BridgeUpdates()
        session = MagicMock()
        device = SimpleNamespace(id=1, dev_id='phone', state='online', service_port=1234)
        session.query.return_value.join.return_value.filter.return_value.one_or_none.return_value = device
        session.query.return_value.filter_by.return_value.one_or_none.return_value = None
        with patch.object(updates, 'session_scope') as scope, patch.object(updates.db, 'get',
                side_effect=[b'a', b'a', b'b', b'b']), patch.object(updates, 'inspect_phone', return_value='current') as probe, \
                patch.object(updates, 'onboard') as install:
            scope.return_value.__enter__.return_value = session
            for _ in range(3):
                worker.check('phone', 1, {'bridge': 'hash'})
            device.service_port += 1
            worker.check('phone', 1, {'bridge': 'hash'})
        self.assertEqual(probe.call_count, 3)
        install.assert_not_called()

    def test_policy_cannot_enroll_by_wildcard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'enrolled.json'
            with patch.dict(os.environ, {'BK_BRIDGE_DEVICES_FILE': str(path)}):
                self.assertEqual(enrolled(), {})
                path.write_text('{"_default":1}')
                with self.assertRaises(ValueError):
                    enrolled()
