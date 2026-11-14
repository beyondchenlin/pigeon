"""Ensure task and reload transport never enables implicit gRPC retries."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from server.bk_device_channel import open_device


class DeviceChannelTests(unittest.TestCase):
    def test_channel_disables_transport_retry_and_uses_one_bounded_deadline(self):
        device=MagicMock()
        old=MagicMock(); raw=MagicMock(); wrapped=MagicMock()
        device.channel=old; device.server='127.0.0.1:1234'; device.session='synthetic-session'
        device._parse_certdata.return_value=(b'key',b'cert',b'ca')
        device._parse_cname.return_value='synthetic-host'
        with patch.dict(os.environ, {'API_PORT':'1234'}), \
             patch('lamda.client.Device',return_value=device), \
             patch('pathlib.Path.read_bytes',return_value=b'synthetic-pem'), \
             patch('grpc.ssl_channel_credentials',return_value='credentials'), \
             patch('grpc.secure_channel',return_value=raw) as secure, \
             patch('grpc.intercept_channel',return_value=wrapped) as intercept:
            result=open_device('phone1',15)
        self.assertIs(result.channel,wrapped)
        old.close.assert_called_once()
        options=dict(secure.call_args.kwargs['options'])
        self.assertEqual(options['grpc.enable_retries'],0)
        self.assertNotIn('grpc.service_config',options)
        deadline=intercept.call_args.args[-1]
        observed=[]
        deadline.intercept_unary_unary(lambda info,request: observed.append((info,request)),
            SimpleNamespace(method='/synthetic',metadata=None,credentials=None,compression=None), 'request')
        self.assertEqual(observed[0][0].timeout,15)
        self.assertFalse(observed[0][0].wait_for_ready)
        for method in ('intercept_unary_stream', 'intercept_stream_unary', 'intercept_stream_stream'):
            observed.clear()
            getattr(deadline,method)(lambda info,request: observed.append((info,request)),
                SimpleNamespace(method='/synthetic',metadata=None,credentials=None,compression=None), 'request')
            self.assertEqual(observed[0][0].timeout,15)

    def test_invalid_identity_is_rejected_before_connection(self):
        with patch('lamda.client.Device') as device:
            with self.assertRaisesRegex(ValueError,'invalid_device'):
                open_device('../outside',15)
            device.assert_not_called()
