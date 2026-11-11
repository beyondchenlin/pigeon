"""Phone-local BK HTTP bridge and arbitration, run as Android shell.

Only this process consumes the loopback credential. It never leaves the phone.
The state file contains task IDs, not tokens or task content. Unknown execution
retains the AI lease until a confirmed terminal receipt is available.
"""
from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import urllib.error
import uuid

TERMINAL = frozenset(('done', 'failed', 'cancelled'))
DEFAULT_INFO = '/sdcard/Android/data/com.beyondchenlin.bk101/files/mcp-usb.json'


def process_identity(pid):
    try:
        return Path('/proc/%s/stat' % pid).read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def manual_owner(socket_fd):
    pid = os.getpid()
    target = os.readlink('/proc/%d/fd/%d' % (pid, socket_fd))
    if not target.startswith('socket:['):
        raise ValueError('Manual control requires a live socket')
    return dict(pid=pid, identity=process_identity(pid), fd=socket_fd, socket=target)


def live_manual(claim):
    # Legacy unowned claims stay fail-closed until an explicit guard activation.
    if not isinstance(claim, dict):
        return True
    try:
        process = Path('/proc/%d' % claim['pid'])
        identity = process.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19]
        if identity != claim['identity']:
            return False
        return os.readlink(process / 'fd' / str(claim['fd'])) == claim['socket']
    except FileNotFoundError:
        return False
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        return True


@contextmanager
def state(root):
    import fcntl
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError('state path is a link')
    with (root / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / 'state.json'
        data = json.loads(path.read_text()) if path.exists() else {'ai': None, 'manual': []}
        data['manual'] = [claim for claim in data['manual'] if live_manual(claim)]
        yield data
        temp = root / 'state.tmp'
        with temp.open('w') as stream:
            json.dump(data, stream); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)


def api(info, method, path, body=None):
    task_path = method in ('GET', 'POST') and re.fullmatch(r'/api/v1(?:/device|/tasks(?:/[a-f0-9-]{36}(?:/cancel)?)?)?', path)
    evidence_path = (method == 'GET' and re.fullmatch(r'/api/v1/evidence(?:/[a-f0-9-]{36}/chunk/[0-9]{1,7})?', path)) or (method == 'POST' and re.fullmatch(r'/api/v1/evidence/[a-f0-9-]{36}/ack', path))
    if not (task_path or evidence_path):
        return {'http': 400, 'body': {'error': {'code': 'unsupported_operation'}}}
    try:
        with open(info) as f:
            connection = json.load(f)
        port = connection['port']
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError()
        headers = {'Authorization': 'Bearer ' + connection['token'], 'Content-Type': 'application/json'}
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
            data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
        # Never follow a redirect with the loopback credential.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            response = opener.open(req, timeout=3)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError()
            return {'http': response.code, 'body': json.loads(raw)}
    except FileNotFoundError:
        return {'http': 409, 'body': {'error': {'code': 'phone_access_disabled'}}}
    except Exception:
        return {'http': 502, 'body': {'error': {'code': 'delivery_unknown'}}}


def prepare_unsecured(info):
    """Opt-in kiosk preparation using Android's normal keyguard dismissal.

    No PIN/password input and no settings changes. Runs under the AI lease before
    submission. Bringing BK forward also lets an OEM-frozen process answer HTTP.
    A ready system state is required; a secure/remaining lock fails closed.
    """
    try:
        for args in (['input', 'keyevent', 'KEYCODE_WAKEUP'], ['wm', 'dismiss-keyguard'],
                     ['am', 'start', '-n', 'com.beyondchenlin.bk101/com.wangzan101.mika.ui.MainActivity']):
            subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=True)
        reply = api(info, 'GET', '/api/v1/device')
        device = reply.get('body', {}).get('device', {})
        return (reply['http'] == 200 and device.get('secure') is False
                and device.get('ready') is True and device.get('helper_available') is True)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        return False


def bridge(request):
    root, info = request['state_dir'], request['info']
    method, path, body = request['method'], request['path'], request.get('body')
    if method == 'POST' and path == '/api/v1/tasks':
        task_id = body.get('request_id') if isinstance(body, dict) else None
        if not isinstance(task_id, str) or str(uuid.UUID(task_id)) != task_id:
            return {'http': 400, 'body': {'error': {'code': 'invalid_id'}}}
        with state(root) as data:
            ready = data.get('guard_identity') and process_identity(data.get('guard_pid')) == data['guard_identity']
            if not ready or data.get('manual') or (data.get('ai') and data['ai'] != task_id):
                return {'http': 409, 'body': {'error': {'code': 'control_busy_or_guard_unavailable'}}}
            if data.get('ai') == task_id:
                # Even a duplicate at the bridge never repeats creation.
                return api(info, 'GET', '/api/v1/tasks/' + task_id)
            data['ai'] = task_id
        # Persist the reservation before dispatch. Manual takeover sees this ID.
        with state(root) as data:
            if data.get('manual'):
                data['ai'] = None
                return {'http': 409, 'body': {'error': {'code': 'manual_takeover'}}}
            if request.get('prepare_unsecured') is True and not prepare_unsecured(info):
                # No POST has happened, so this is a definite refusal, not an
                # ambiguous execution. A later deliberate request may try again.
                data['ai'] = None
                return {'http': 409, 'body': {'error': {'code': 'device_not_ready'}}}
            reply = api(info, method, path, body)
            receipt = reply.get('body', {})
            terminal = (reply['http'] in (200, 201) and isinstance(receipt, dict)
                        and receipt.get('task_id') == task_id and receipt.get('status') in TERMINAL)
            if terminal or reply['http'] in (400, 401, 403, 409, 422, 429):
                data['ai'] = None
            return reply
    reply = api(info, method, path, body)
    task = reply.get('body', {})
    if reply['http'] == 200 and task.get('status') in TERMINAL:
        with state(root) as data:
            if data.get('ai') == task.get('task_id'):
                data['ai'] = None
    return reply


def activate_guard(root):
    with state(root) as data:
        data.update(guard_pid=os.getpid(), guard_identity=process_identity(os.getpid()), manual=[])


def enter_manual(root, info=DEFAULT_INFO, socket_fd=None):
    claim = str(uuid.uuid4())
    owner = dict(id=claim, **manual_owner(socket_fd)) if socket_fd is not None else claim
    with state(root) as data:
        data['manual'].append(owner)
        task_id = data.get('ai')
    if not task_id:
        return claim
    try:
        # Query before cancellation; starting may not yet have a live run.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            reply = api(info, 'GET', '/api/v1/tasks/' + task_id)
            task = reply.get('body', {})
            if reply['http'] == 200 and task.get('task_id') == task_id and task.get('status') in TERMINAL:
                with state(root) as data:
                    if data.get('ai') == task_id:
                        data['ai'] = None
                return claim
            if reply['http'] == 200:
                api(info, 'POST', '/api/v1/tasks/' + task_id + '/cancel', {})
            time.sleep(.2)
        raise RuntimeError('AI stop is unconfirmed; input blocked')
    except BaseException:
        leave_manual(root, claim)
        raise


def leave_manual(root, claim):
    with state(root) as data:
        data['manual'] = [c for c in data['manual'] if (c.get('id') if isinstance(c, dict) else c) != claim]


def record_admission(request, reply):
    """Bounded diagnostic receipt, never credentials, goal text, or a retry trigger."""
    if request.get('method') != 'POST' or request.get('path') != '/api/v1/tasks':
        return
    try:
        task_id = request.get('body', {}).get('request_id')
        if not isinstance(task_id, str) or str(uuid.UUID(task_id)) != task_id:
            return
        body = reply.get('body', {})
        error = body.get('error', {}).get('code') if isinstance(body, dict) else None
        if error not in ('control_busy_or_guard_unavailable', 'manual_takeover', 'device_not_ready',
                         'phone_access_disabled', 'delivery_unknown', 'conflict', 'unavailable'):
            error = 'other' if error else None
        value = dict(task_id=task_id, http=reply.get('http'), error=error, at=time.time())
        root = Path(request['state_dir'])
        with state(root):
            temporary = root / 'last-admission.tmp'
            with temporary.open('w') as stream:
                json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, root / 'last-admission.json')
    except Exception:
        # Diagnostics cannot change the action receipt or cause a new submission.
        pass


if __name__ == '__main__':
    import sys
    try:
        payload = json.loads(base64.b64decode(sys.argv[1], validate=True))
        reply = bridge(payload)
        record_admission(payload, reply)
        print(json.dumps(reply, ensure_ascii=False))
    except Exception:
        print(json.dumps({'http': 502, 'body': {'error': {'code': 'bridge_failure_unknown'}}}))
