import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, MagicMock
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('heartbeat_compat', Path(__file__).resolve().parents[1]/'deploy/pilot/heartbeat_compat.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HeartbeatValidation(unittest.TestCase):
    def message(self, **changes):
        return dict(type='task/status', device_id='test-device', timestamp=1_000_000, data={}, **changes)

    def check(self, message, topic='device/test-device/event', retained=False):
        return module.eligible_identity(topic, retained, message, 1000)

    def test_fresh_authenticated_topic_is_live(self):
        self.assertEqual(self.check(self.message()), 'test-device')

    def test_other_topic_or_retained_packet_cannot_resurrect_device(self):
        self.assertIsNone(self.check(self.message(), topic='device/other/event'))
        self.assertIsNone(self.check(self.message(), retained=True))

    def test_stale_future_and_missing_time_are_rejected(self):
        for stamp in [None, True, float('nan'), 800_000, 1_031_000, '1000000']:
            with self.subTest(stamp=stamp):
                m = self.message(); m['timestamp'] = stamp
                self.assertIsNone(self.check(m))

    def test_disconnect_and_unknown_event_cannot_mark_online(self):
        for kind in ['bye', 'unknown', 'cloud/helo']:
            m = self.message(); m['type'] = kind
            self.assertIsNone(self.check(m))

    def test_malformed_payload_and_id_are_rejected(self):
        self.assertIsNone(self.check(None))
        m = self.message(); m['data'] = None
        self.assertIsNone(self.check(m))
        m = self.message(); m['device_id'] = 'other/event'
        self.assertIsNone(self.check(m))


class GatewayRenewal(unittest.TestCase):
    def test_live_forward_uses_original_authenticated_gateway_writer(self):
        d = SimpleNamespace(mode='forward', state='online', service_port=47105)
        connect, publish = MagicMock(), Mock()
        self.assertTrue(module.refresh_forward_gateway(d, connect, publish))
        connect.assert_called_once_with(('127.0.0.1', 47105), timeout=0.3)
        publish.assert_called_once_with(d, '127.0.0.1', 47105)

    def test_disconnected_forward_cannot_refresh_route(self):
        d = SimpleNamespace(mode='forward', state='online', service_port=47105)
        connect, publish = Mock(side_effect=ConnectionRefusedError), Mock()
        self.assertFalse(module.refresh_forward_gateway(d, connect, publish))
        publish.assert_not_called()

    def test_other_modes_offline_and_invalid_ports_cannot_create_route(self):
        for mode,state,port in [('p2p','online',47105),('forward','offline',47105),('forward','online',None),('forward','online',True),('forward','online',22),('forward','online',70000)]:
            connect,publish=Mock(),Mock()
            d=SimpleNamespace(mode=mode,state=state,service_port=port)
            self.assertFalse(module.refresh_forward_gateway(d,connect,publish))
            connect.assert_not_called();publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
