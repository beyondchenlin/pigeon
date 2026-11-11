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


def refusal_code(reply):
    """Keep bounded, known admission reasons; never persist raw phone messages."""
    generic = 'phone_refused_' + str(reply['http'])
    body = reply.get('body')
    error = body.get('error') if isinstance(body, dict) else None
    reason = error.get('code') if isinstance(error, dict) else None
    allowed = ('control_busy_or_guard_unavailable', 'manual_takeover',
               'device_not_ready', 'phone_access_disabled', 'conflict',
               'invalid_argument', 'unauthorized', 'forbidden')
    return generic + ':' + reason if isinstance(reason, str) and reason in allowed else generic


def mapping(domain):
    path = Path(os.environ.get('BK_AI_DEVICES_FILE', '/user/phone-pilot/bk-ai-devices.json'))
    try:
        config = json.loads(path.read_text())[domain]
        for key in ('python', 'bridge', 'info', 'state_dir', 'library'):
            value = config[key]
            if not isinstance(value, str) or not value.startswith('/') or not re.fullmatch(r'[/A-Za-z0-9_.-]+', value):
                raise ValueError('invalid path')
        if type(config.get('prepare_unsecured', False)) is not bool:
            raise ValueError('invalid preparation flag')
        return config
    except (OSError, KeyError, ValueError, TypeError):
        raise TransportError('device_not_configured') from None


def call(domain, method, path, body=None):
    import grpc
    from lamda.client import Device
    config = mapping(domain)
    prepare = method == 'POST' and path == '/api/v1/tasks' and config.get('prepare_unsecured', False)
    payload = base64.b64encode(json.dumps(dict(method=method, path=path, body=body,
        info=config['info'], state_dir=config['state_dir'], prepare_unsecured=prepare)).encode()).decode()
    command = 'export LD_LIBRARY_PATH=' + shlex.quote(config['library']) + '; ' + shlex.join([
        config['python'], config['bridge'], payload])

    class Deadline(grpc.UnaryUnaryClientInterceptor):
        def intercept_unary_unary(self, continuation, details, request):
            Call = collections.namedtuple('Call', 'method timeout metadata credentials wait_for_ready compression')
            return continuation(Call(details.method, 25 if prepare else 15, details.metadata, details.credentials, False, None), request)

    device = Device('127.0.0.1', port=int(os.environ['API_PORT']),
                    certificate=str(Path('/user/certificates') / (domain + '.pem')))
    device.channel = grpc.intercept_channel(device.channel, Deadline())
    try:
        if method == 'POST' and path == '/api/v1/tasks':
            # FIRERPA 10.9's startup listener can suppress Android accessibility
            # until its UI driver is initialized. This read initializes the
            # normal coexistence path; shell-only RPC does not initialize it.
            device.device_info()
        reply = device.execute_script(command, timeout=22 if prepare else 12)
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
