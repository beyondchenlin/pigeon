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
               'invalid_argument', 'unauthorized', 'forbidden',
               'unsupported_execution', 'request_signature_unavailable')
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


def wait_duration(method, path, body):
    if method != 'POST' or not re.fullmatch(r'/api/v1/tasks/[a-f0-9-]{36}/wait', path):
        return None
    if (not isinstance(body, dict) or set(body) != {'after_revision', 'timeout_ms'}
            or type(body['after_revision']) is not int or not 0 <= body['after_revision'] <= 2**63 - 1
            or type(body['timeout_ms']) is not int or not 1000 <= body['timeout_ms'] <= 20000):
        raise TransportError('invalid_wait')
    # FIRERPA's script timeout is a protobuf integer; round up subsecond waits.
    return (body['timeout_ms'] + 999) // 1000


def call(domain, method, path, body=None):
    wait_s = wait_duration(method, path, body)
    import grpc
    from lamda.client import Device
    config = mapping(domain)
    prepare = method == 'POST' and path == '/api/v1/tasks' and config.get('prepare_unsecured', False)
    payload = base64.b64encode(json.dumps(dict(method=method, path=path, body=body,
        info=config['info'], state_dir=config['state_dir'], prepare_unsecured=prepare)).encode()).decode()
    command = 'export LD_LIBRARY_PATH=' + shlex.quote(config['library']) + '; ' + shlex.join([
        config['python'], config['bridge'], payload])
    # Each wait owns its own authenticated connection. The caller schedules it
    # outside the control pool; these deadlines do not enlarge stop/query calls.
    script_timeout = wait_s + 8 if wait_s is not None else (22 if prepare else 12)
    rpc_timeout = script_timeout + 3

    class Deadline(grpc.UnaryUnaryClientInterceptor):
        def intercept_unary_unary(self, continuation, details, request):
            Call = collections.namedtuple('Call', 'method timeout metadata credentials wait_for_ready compression')
            return continuation(Call(details.method, rpc_timeout, details.metadata, details.credentials, False, None), request)

    device = Device('127.0.0.1', port=int(os.environ['API_PORT']),
                    certificate=str(Path('/user/certificates') / (domain + '.pem')))
    device.channel = grpc.intercept_channel(device.channel, Deadline())
    try:
        if method == 'POST' and path == '/api/v1/tasks':
            # FIRERPA 10.9's startup listener can suppress Android accessibility
            # until its UI driver is initialized. This read initializes the
            # normal coexistence path; shell-only RPC does not initialize it.
            device.device_info()
        reply = device.execute_script(command, timeout=script_timeout)
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
