"""Authenticated, bounded FIRERPA channel with transport retries disabled."""
import collections
from pathlib import Path
import re


def open_device(domain, timeout):
    if not isinstance(domain, str) or not re.fullmatch(r'[a-z0-9]{1,64}', domain):
        raise ValueError('invalid_device')
    if not isinstance(timeout, int) or not 1 <= timeout <= 120:
        raise ValueError('invalid_timeout')
    import grpc
    from lamda.client import (Device, ClientSessionMetadataInterceptor,
                              GrpcRemoteExceptionInterceptor, ClientLoggingInterceptor)
    import os

    class Deadline(grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor,
                   grpc.StreamUnaryClientInterceptor, grpc.StreamStreamClientInterceptor):
        def _call(self, continuation, details, request):
            call = collections.namedtuple('Call', 'method timeout metadata credentials wait_for_ready compression')
            return continuation(call(details.method, timeout, details.metadata, details.credentials, False, None), request)

        intercept_unary_unary = _call
        intercept_unary_stream = _call
        intercept_stream_unary = _call
        intercept_stream_stream = _call

    certificate = str(Path('/user/certificates') / (domain + '.pem'))
    device = Device('127.0.0.1', port=int(os.environ['API_PORT']), certificate=certificate)
    old_channel = device.channel
    new_channel = None
    try:
        key, crt, ca = device._parse_certdata(Path(certificate).read_bytes())
        credentials = grpc.ssl_channel_credentials(root_certificates=ca, certificate_chain=crt, private_key=key)
        options = (('grpc.ssl_target_name_override', device._parse_cname(crt)),
                   ('grpc.max_send_message_length', 64 * 1024 * 1024),
                   ('grpc.max_receive_message_length', 128 * 1024 * 1024),
                   ('grpc.keepalive_time_ms', 60000), ('grpc.keepalive_timeout_ms', 20000),
                   ('grpc.keepalive_permit_without_calls', True), ('grpc.enable_http_proxy', 0),
                   ('grpc.enable_retries', 0))
        new_channel = grpc.secure_channel(device.server, credentials, options=options)
        device.channel = grpc.intercept_channel(new_channel,
            ClientSessionMetadataInterceptor(device.session), GrpcRemoteExceptionInterceptor(),
            ClientLoggingInterceptor(), Deadline())
        device._chan = new_channel
        old_channel.close()
        return device
    except BaseException:
        if new_channel is not None:
            new_channel.close()
        old_channel.close()
        raise
