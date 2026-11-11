"""Authenticated FIRERPA RPC to BK's phone-local HTTP API. No credential export."""
import base64
import collections
import json
import os
from pathlib import Path
import re
import shlex


class TransportError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def mapping(domain):
    path = Path(os.environ.get('BK_AI_DEVICES_FILE', '/user/phone-pilot/bk-ai-devices.json'))
    try:
        config = json.loads(path.read_text())[domain]
        for key in ('python', 'bridge', 'info', 'state_dir', 'library'):
            value = config[key]
            if not isinstance(value, str) or not value.startswith('/') or not re.fullmatch(r'[/A-Za-z0-9_.-]+', value):
                raise ValueError('invalid path')
        return config
    except (OSError, KeyError, ValueError, TypeError):
        raise TransportError('device_not_configured') from None


def call(domain, method, path, body=None):
    import grpc
    from lamda.client import Device
    config = mapping(domain)
    payload = base64.b64encode(json.dumps(dict(method=method, path=path, body=body,
        info=config['info'], state_dir=config['state_dir'])).encode()).decode()
    command = 'export LD_LIBRARY_PATH=' + shlex.quote(config['library']) + '; ' + shlex.join([
        config['python'], config['bridge'], payload])

    class Deadline(grpc.UnaryUnaryClientInterceptor):
        def intercept_unary_unary(self, continuation, details, request):
            Call = collections.namedtuple('Call', 'method timeout metadata credentials wait_for_ready compression')
            return continuation(Call(details.method, 15, details.metadata, details.credentials, False, None), request)

    device = Device('127.0.0.1', port=int(os.environ['API_PORT']),
                    certificate=str(Path('/user/certificates') / (domain + '.pem')))
    device.channel = grpc.intercept_channel(device.channel, Deadline())
    try:
        if method == 'POST' and path == '/api/v1/tasks':
            # FIRERPA 10.9's startup listener can suppress Android accessibility
            # until its UI driver is initialized. This read initializes the
            # normal coexistence path; shell-only RPC does not initialize it.
            device.device_info()
        reply = device.execute_script(command, timeout=12)
        # stderr is deliberately never logged: native errors may contain secrets.
        if len(reply.stdout) > 1024 * 1024:
            raise TransportError('invalid_response')
        result = json.loads(reply.stdout)
        if not isinstance(result, dict) or not isinstance(result.get('http'), int):
            raise TransportError('invalid_response')
        return result
    except TransportError:
        raise
    except Exception:
        raise TransportError('connection_unknown') from None
    finally:
        device.channel.close()
