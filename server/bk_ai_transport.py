"""Authenticated FIRERPA RPC to BK's phone-local HTTP API. No credential export."""
import base64
import json
import os
from pathlib import Path
import re
import shlex

EVIDENCE_BATCH = re.compile(r'/api/v1/evidence/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})/chunks/([0-9]{1,7})')
NATIVE_INFO = '/sdcard/Android/data/com.beyondchenlin.bk101.nativemcp/files/mcp-usb.json'


def evidence_batch_request(method, path, body):
    if '/chunks/' not in path:
        return False
    match = EVIDENCE_BATCH.fullmatch(path)
    if (method != 'GET' or body is not None or match is None
            or int(match.group(2)) % (128 * 1024) != 0
            or int(match.group(2)) >= 768 * 1024):
        raise TransportError('invalid_evidence_batch')
    return True


class TransportError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def open_native_app(domain):
    """Open the installed app once without changing its connection switch."""
    from .bk_device_channel import open_device
    device = None
    try:
        device = open_device(domain, 12)
        reply = device.execute_script(
            'am start -n com.beyondchenlin.bk101.nativemcp/com.wangzan101.mika.ui.MainActivity', timeout=10)
        return reply.exitstatus == 0
    except Exception:
        raise TransportError('app_launch_unconfirmed') from None
    finally:
        if device is not None:
            device.channel.close()


def refusal_code(reply):
    """Keep bounded, known admission reasons; never persist raw phone messages."""
    generic = 'phone_refused_' + str(reply['http'])
    body = reply.get('body')
    error = body.get('error') if isinstance(body, dict) else None
    reason = error.get('code') if isinstance(error, dict) else None
    allowed = ('control_busy_or_guard_unavailable', 'manual_takeover',
               'device_not_ready', 'phone_access_disabled', 'conflict',
               'invalid_argument', 'unauthorized', 'forbidden',
               'unsupported_execution', 'request_signature_unavailable', 'guard_upgrade_required')
    return generic + ':' + reason if isinstance(reason, str) and reason in allowed else generic


def mapping(domain):
    path = Path(os.environ.get('BK_AI_DEVICES_FILE', '/user/phone-pilot/bk-ai-devices.json'))
    try:
        configurations = json.loads(path.read_text())
        config = configurations.get(domain, configurations.get('_default'))
        if not isinstance(config, dict):
            raise ValueError('missing configuration')
        if config.get('runtime_env') is True:
            if set(config) - {'runtime_env', 'info', 'prepare_unsecured'} or config.get('info') != 'auto':
                raise ValueError('invalid runtime configuration')
            if type(config.get('prepare_unsecured', False)) is not bool:
                raise ValueError('invalid preparation flag')
            return config
        for key in ('python', 'bridge', 'info', 'state_dir', 'library'):
            value = config[key]
            if key == 'info' and value == 'auto':
                continue
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


def call(domain, method, path, body=None, *, discover_native=False, task_info=None, create_native=False):
    wait_s = wait_duration(method, path, body)
    batched_evidence = evidence_batch_request(method, path, body)
    from .bk_device_channel import open_device
    config = mapping(domain)
    if create_native:
        if method != 'POST' or path != '/api/v1/tasks' or discover_native or task_info is not None:
            raise TransportError('invalid_task_endpoint')
        config = dict(config, info='auto')
    if task_info is not None:
        if task_info != NATIVE_INFO or discover_native or (method == 'POST' and path == '/api/v1/tasks'):
            raise TransportError('invalid_task_endpoint')
        config = dict(config, info=task_info)
    if discover_native:
        if method != 'GET' or path != '/api/v1/native-discovery' or body is not None:
            raise TransportError('invalid_discovery')
        config = dict(config, info=NATIVE_INFO)
    prepare = method == 'POST' and path == '/api/v1/tasks' and config.get('prepare_unsecured', False)
    request = dict(method=method, path=path, body=body, info=config['info'], prepare_unsecured=prepare)
    if config.get('runtime_env') is True:
        # ROOTDIR and USRDIR belong to this phone's authenticated FIRERPA
        # process; never copy another phone's generated runtime path.
        wrapper = ("import os,sys,json,base64; r=json.loads(base64.b64decode(sys.argv[1])); "
                   "u=os.environ['USRDIR']; root=os.environ['ROOTDIR']; "
                   "r['state_dir']=os.path.join(u,'phone-pilot','bk-ai'); "
                   "p=base64.b64encode(json.dumps(r).encode()).decode(); "
                   "py=os.path.join(root,'bin','python3'); "
                   "os.execv(py,[py,os.path.join(u,'modules','bk_ai_phone_mcp_candidate.py'),p])")
        payload = base64.b64encode(json.dumps(request).encode()).decode()
        command = 'export LD_LIBRARY_PATH="$ROOTDIR/lib"; exec "$ROOTDIR/bin/python3" -c ' + shlex.quote(wrapper) + ' ' + shlex.quote(payload)
    else:
        request['state_dir'] = config['state_dir']
        payload = base64.b64encode(json.dumps(request).encode()).decode()
        command = 'export LD_LIBRARY_PATH=' + shlex.quote(config['library']) + '; ' + shlex.join([
            config['python'], config['bridge'], payload])
    # Each wait owns its own authenticated connection. The caller schedules it
    # outside the control pool; these deadlines do not enlarge stop/query calls.
    # A batch performs at most four bounded three-second loopback reads. It does
    # not initialize UI, acquire a screen lease, or share a stop/query connection.
    script_timeout = wait_s + 8 if wait_s is not None else (22 if prepare else (18 if batched_evidence else 12))
    rpc_timeout = script_timeout + 3

    device = None
    try:
        device = open_device(domain, rpc_timeout)
        if (method == 'POST' and path == '/api/v1/tasks') or discover_native:
            # FIRERPA 10.9's startup listener can suppress Android accessibility
            # until its UI driver is initialized. This read initializes the
            # normal coexistence path; shell-only RPC does not initialize it.
            # Native discovery needs the same read-only initialization, so
            # an enabled but disconnected accessibility service can rebind.
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
        if device is not None:
            device.channel.close()
