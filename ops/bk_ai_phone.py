"""Phone-local BK HTTP bridge and arbitration, run as Android shell.

Only this process consumes the loopback credential. It never leaves the phone.
The state file contains task IDs and the execution contract, never credentials
or goal text. Unknown execution retains the AI lease until a confirmed terminal
receipt is available.
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
import hashlib

TERMINAL = frozenset(('done', 'failed', 'cancelled'))
DEFAULT_INFO = '/sdcard/Android/data/com.beyondchenlin.bk101/files/mcp-usb.json'
EVIDENCE_CHUNK = 128 * 1024
EVIDENCE_MAX_IMAGE = 768 * 1024
EVIDENCE_BATCH_CHUNKS = 4
EVIDENCE_MAX_REPLY = 1024 * 1024
EVIDENCE_BATCH = re.compile(r'/api/v1/evidence/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})/chunks/([0-9]{1,7})')


NATIVE_MCP_PACKAGE = 'com.beyondchenlin.bk101.nativemcp'
PHONE_PACKAGES = ('com.beyondchenlin.bk101', 'com.beyondchenlin.bk101.mcp', NATIVE_MCP_PACKAGE)
INFO_PACKAGES = {'/sdcard/Android/data/' + package + '/files/mcp-usb.json': package for package in PHONE_PACKAGES}
NATIVE_INFO = next(path for path, package in INFO_PACKAGES.items() if package == NATIVE_MCP_PACKAGE)


def valid_info(info):
    return isinstance(info, str) and info in INFO_PACKAGES


def lease_key(data):
    return json.dumps({key: data[key] for key in ('ai', 'ai_info', 'ai_signature', 'ai_execution')
                       if key in data}, sort_keys=True, separators=(',', ':'))


def lease_info(data, supplied=None):
    # Missing is the legacy format. A present but invalid binding fails closed.
    if 'ai_info' in data:
        target = data['ai_info']
        if supplied is not None and supplied != target:
            raise ValueError('task_package_conflict')
    else:
        target = DEFAULT_INFO if supplied is None else supplied
    if not valid_info(target):
        raise ValueError('invalid_task_package')
    return target


def release_ai(data):
    data['ai'] = None
    for key in ('ai_info', 'ai_signature', 'ai_execution', 'answer_attempts'):
        data.pop(key, None)


def has_manual(data, claim):
    return any((value.get('id') if isinstance(value, dict) else value) == claim
               for value in data['manual'])


def process_identity(pid):
    try:
        return Path('/proc/%s/stat' % pid).read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def live_guard_ready(root):
    """Read the atomic guard snapshot without creating or changing files."""
    try:
        path = Path(root)
        if path.is_symlink():
            return False
        data = json.loads((path / 'state.json').read_text())
        pid, identity = data.get('guard_pid'), data.get('guard_identity')
        return bool(identity and data.get('guard_protocol') == {'version': 2, 'pid': pid, 'identity': identity}
                    and process_identity(pid) == identity)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def guard_reload_pending(root):
    """An installed update must not use the old guard before confirmation."""
    try:
        manifest = Path(root).parent / 'bk-native-bridge-install.json'
        if manifest.is_symlink():
            return True
        if not manifest.exists():
            return False  # Older installations predate the managed manifest.
        data = json.loads(manifest.read_text())
        return not (data.get('installed') is True and data.get('version') == 2
                    and data.get('reload_needed') is False)
    except (OSError, ValueError, TypeError, AttributeError):
        return True


def native_guard_ready(root):
    return live_guard_ready(root) and not guard_reload_pending(root)


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


def wait_duration(method, path, body):
    if method != 'POST' or not re.fullmatch(r'/api/v1/tasks/[a-f0-9-]{36}/wait', path):
        return None
    if (not isinstance(body, dict) or set(body) != {'after_revision', 'timeout_ms'}
            or type(body['after_revision']) is not int or not 0 <= body['after_revision'] <= 2**63 - 1
            or type(body['timeout_ms']) is not int or not 1000 <= body['timeout_ms'] <= 20000):
        raise ValueError('invalid_wait')
    return body['timeout_ms'] / 1000


def api(info, method, path, body=None):
    # The pilot uses the stock task tools for all callers, including manual takeover.
    if INFO_PACKAGES.get(info) == NATIVE_MCP_PACKAGE:
        from bk_native_mcp import task_api
        return task_api(info, method, path, body)
    try:
        wait_s = wait_duration(method, path, body)
    except ValueError:
        return {'http': 400, 'body': {'error': {'code': 'invalid_argument'}}}
    task_path = method in ('GET', 'POST') and re.fullmatch(r'/api/v1(?:/device|/tasks(?:/[a-f0-9-]{36}(?:/(?:cancel|answer))?)?)?', path)
    evidence_path = (method == 'GET' and re.fullmatch(r'/api/v1/evidence(?:/[a-f0-9-]{36}/chunk/[0-9]{1,7})?', path)) or (method == 'POST' and re.fullmatch(r'/api/v1/evidence/[a-f0-9-]{36}/ack', path))
    capabilities_path = method == 'GET' and path == '/api/v1/capabilities'
    if not (task_path or evidence_path or wait_s is not None or capabilities_path):
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
            # Waiting uses a separate bridge process and never holds the state
            # file lock, so cancellation and admission checks remain available.
            response = opener.open(req, timeout=3 if wait_s is None else wait_s + 2)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError()
            return {'http': response.code, 'body': {} if response.code == 204 else json.loads(raw)}
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
        package = INFO_PACKAGES.get(info)
        if not package:
            return False
        component = package + '/com.wangzan101.mika.ui.MainActivity'
        for args in (['input', 'keyevent', 'KEYCODE_WAKEUP'], ['wm', 'dismiss-keyguard'],
                     ['am', 'start', '-n', component]):
            subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=True)
        reply = api(info, 'GET', '/api/v1/device')
        device = reply.get('body', {}).get('device', {})
        return (reply['http'] == 200 and device.get('secure') is False
                and device.get('ready') is True and device.get('helper_available') is True)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        return False


def evidence_batch(info, method, path, body):
    """Read up to four existing chunks locally; never accept a caller operation list.

    The loopback credential stays inside api(). No task or ACK operation is
    permitted here, and no arbitration lock is held while image bytes are read.
    The Android application's existing 128 KiB endpoint remains unchanged.
    """
    def failure(code, http=400):
        return {'http': http, 'body': {'error': {'code': code}}}
    match = EVIDENCE_BATCH.fullmatch(path)
    if method != 'GET' or body is not None or match is None:
        return failure('invalid_evidence_batch')
    eid, offset = match.group(1), int(match.group(2))
    if offset % EVIDENCE_CHUNK or offset >= EVIDENCE_MAX_IMAGE:
        return failure('invalid_evidence_batch')
    chunks, size, digest = [], None, None
    started = time.monotonic()
    for _ in range(EVIDENCE_BATCH_CHUNKS):
        if time.monotonic() - started >= 14:
            return failure('evidence_batch_deadline', 502)
        reply = api(info, 'GET', '/api/v1/evidence/' + eid + '/chunk/' + str(offset))
        if reply.get('http') != 200:
            return failure('evidence_chunk_unavailable', 502)
        item = reply.get('body')
        try:
            if (not isinstance(item, dict) or item.get('evidence_id') != eid
                    or type(item.get('offset')) is not int or item['offset'] != offset
                    or type(item.get('size')) is not int or not offset < item['size'] <= EVIDENCE_MAX_IMAGE
                    or not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])
                    or not isinstance(item.get('data'), str)
                    or len(item['data']) > ((EVIDENCE_CHUNK + 2) // 3) * 4):
                raise ValueError()
            if size is None:
                size, digest = item['size'], item['sha256']
            if item['size'] != size or item['sha256'] != digest:
                raise ValueError()
            raw = base64.b64decode(item['data'], validate=True)
            if len(raw) != min(EVIDENCE_CHUNK, size - offset):
                raise ValueError()
        except (ValueError, TypeError):
            return failure('invalid_evidence_chunk', 502)
        # Project only protocol fields; arbitrary phone fields never become a
        # generic response tunnel or advertise bridge capabilities.
        chunks.append({key: item[key] for key in ('evidence_id', 'offset', 'size', 'sha256', 'data')})
        offset += len(raw)
        if offset == size:
            break
    result = {'http': 200, 'body': {'chunks': chunks}}
    if len(json.dumps(result, separators=(',', ':')).encode()) > EVIDENCE_MAX_REPLY:
        return failure('oversized_evidence_batch', 502)
    return result


def submission_signature(body):
    """Bind the reserved execution to every supplied semantic parameter."""
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def terminal_receipt(reply, task_id, execution=None):
    """A status label alone cannot release a contracted task's screen lease."""
    body = reply.get('body')
    if (reply.get('http') not in (200, 201) or not isinstance(body, dict)
            or body.get('task_id') != task_id or body.get('status') not in TERMINAL):
        return False
    if execution is None:
        return True  # Legacy tasks retain their established terminal protocol.
    result = body.get('execution_result')
    return (body.get('execution') == execution and isinstance(result, dict)
            and result.get('execution_finished') is True
            and type(result.get('finished_at_ms')) is int and result['finished_at_ms'] > 0
            and (body['status'] != 'done' or (result.get('duration_reached') is True
                                            and result.get('business_success') is True)))


def bridge(request):
    root, info = request['state_dir'], request['info']
    method, path, body = request['method'], request['path'], request.get('body')
    automatic = info == 'auto'
    task_id = None
    if automatic:
        if method == 'POST' and path == '/api/v1/tasks' and isinstance(body, dict):
            task_id = body.get('request_id')
        else:
            match = re.fullmatch(r'/api/v1/tasks/([a-f0-9-]{36})(?:/(?:cancel|answer|wait))?', path)
            task_id = match.group(1) if match else None
        try:
            if not isinstance(task_id, str) or str(uuid.UUID(task_id)) != task_id:
                raise ValueError()
        except (ValueError, AttributeError):
            return {'http': 400, 'body': {'error': {'code': 'invalid_argument'}}}
        with state(root) as data:
            info = data.get('task_info', {}).get(task_id)
        if info is None and method == 'POST' and path == '/api/v1/tasks':
            from bk_native_mcp import probe
            if probe(NATIVE_INFO).get('state') != 'connected':
                return {'http': 409, 'body': {'error': {'code': 'device_not_ready'}}}
            info = NATIVE_INFO
        if not valid_info(info):
            return {'http': 409, 'body': {'error': {'code': 'task_package_unknown'}}}
    if not valid_info(info):
        return {'http': 400, 'body': {'error': {'code': 'invalid_argument'}}}
    if method == 'GET' and path == '/api/v1/native-discovery' and body is None:
        if INFO_PACKAGES.get(info) != NATIVE_MCP_PACKAGE:
            return {'http': 400, 'body': {'error': {'code': 'invalid_argument'}}}
        from bk_native_mcp import probe
        result = probe(info)
        if result.get('state') == 'connected' and not native_guard_ready(root):
            result = dict(result, state='guard_not_ready')
        return {'http': 200, 'body': result}
    if '/chunks/' in path:
        return evidence_batch(info, method, path, body)
    if method == 'POST' and re.fullmatch(r'/api/v1/tasks/[a-f0-9-]{36}/answer', path):
        return answer_once(root, info, path, body)
    if method == 'POST' and path == '/api/v1/tasks':
        task_id = body.get('request_id') if isinstance(body, dict) else None
        try:
            if not isinstance(task_id, str) or str(uuid.UUID(task_id)) != task_id:
                raise ValueError()
            signature = submission_signature(body)
        except (ValueError, TypeError, AttributeError):
            return {'http': 400, 'body': {'error': {'code': 'invalid_argument'}}}
        with state(root) as data:
            if automatic:
                previous_info = data.get('task_info', {}).get(task_id)
                if previous_info is not None and previous_info != info:
                    return {'http': 409, 'body': {'error': {'code': 'conflict'}}}
                if previous_info is not None and data.get('ai') != task_id:
                    if data.get('task_signature', {}).get(task_id) != signature:
                        return {'http': 409, 'body': {'error': {'code': 'conflict'}}}
                    # A durable binding means creation may already have been
                    # attempted; never re-submit after an uncertain outcome.
                    return api(info, 'GET', '/api/v1/tasks/' + task_id)
            ready = data.get('guard_identity') and process_identity(data.get('guard_pid')) == data['guard_identity']
            guard_protocol = data.get('guard_protocol')
            if automatic and guard_protocol != {'version': 2, 'pid': data.get('guard_pid'),
                                                'identity': data.get('guard_identity')}:
                return {'http': 409, 'body': {'error': {'code': 'guard_upgrade_required'}}}
            if automatic and guard_reload_pending(root):
                return {'http': 409, 'body': {'error': {'code': 'guard_upgrade_required'}}}
            if not ready or data.get('manual') or (data.get('ai') and data['ai'] != task_id):
                return {'http': 409, 'body': {'error': {'code': 'control_busy_or_guard_unavailable'}}}
            if data.get('ai') == task_id:
                try:
                    lease_info(data, info)
                except ValueError:
                    return {'http': 409, 'body': {'error': {'code': 'conflict'}}}
                # Older reservations remain queryable, but cannot safely accept
                # a POST whose original semantic parameters were not recorded.
                previous = data.get('ai_signature')
                if not previous or previous != signature:
                    code = 'conflict' if previous else 'request_signature_unavailable'
                    return {'http': 409, 'body': {'error': {'code': code}}}
                # Even an identical duplicate never repeats creation.
                reply = api(info, 'GET', '/api/v1/tasks/' + task_id)
                if terminal_receipt(reply, task_id, data.get('ai_execution')):
                    release_ai(data)
                return reply
            data['ai'] = task_id
            if automatic:
                data.setdefault('task_info', {})[task_id] = info
                data.setdefault('task_signature', {})[task_id] = signature
            data['ai_info'] = info
            data['ai_signature'] = signature
            data['ai_execution'] = body.get('execution')
            data['answer_attempts'] = {}
            reservation = lease_key(data)
        # Persist the reservation before dispatch. Manual takeover sees this ID.
        with state(root) as data:
            if lease_key(data) != reservation:
                return {'http': 409, 'body': {'error': {'code': 'conflict'}}}
            if data.get('manual'):
                release_ai(data)
                return {'http': 409, 'body': {'error': {'code': 'manual_takeover'}}}
            if request.get('prepare_unsecured') is True and not prepare_unsecured(info):
                # No POST has happened, so this is a definite refusal, not an
                # ambiguous execution. A later deliberate request may try again.
                release_ai(data)
                return {'http': 409, 'body': {'error': {'code': 'device_not_ready'}}}
            reply = api(info, method, path, body)
            terminal = terminal_receipt(reply, task_id, data.get('ai_execution'))
            if terminal or reply['http'] in (400, 401, 403, 409, 422, 429):
                release_ai(data)
            return reply
    requested = re.fullmatch(r'/api/v1/tasks/([a-f0-9-]{36})(?:/(?:cancel|answer|wait))?', path)
    reservation = None
    if requested:
        with state(root) as data:
            if data.get('ai') == requested.group(1):
                try:
                    lease_info(data, info)
                except ValueError:
                    return {'http': 409, 'body': {'error': {'code': 'conflict'}}}
                reservation = lease_key(data)
    reply = api(info, method, path, body)
    if method == 'GET' and path == '/api/v1/evidence' and reply.get('http') == 200:
        # Authenticate through the normal phone API first. The capability is
        # outside its body, so a phone-supplied manifest field cannot enable it.
        reply = dict(reply, bridge={'evidence_chunk_batch_version': 1})
    task = reply.get('body', {})
    if requested and reservation is not None and isinstance(task, dict) and task.get('status') in TERMINAL:
        task_id = requested.group(1)
        with state(root) as data:
            if lease_key(data) == reservation and terminal_receipt(reply, task_id, data.get('ai_execution')):
                release_ai(data)
    return reply


def question_key(body):
    if body.get('status') != 'waiting_for_answer' or body.get('answerable') is not True or not body.get('question'):
        return None
    value = {k: body.get(k) for k in ('task_id', 'conversation', 'started_at_ms', 'question', 'options')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def answer_once(root, info, path, body):
    def refused(code):
        return {'http': 409, 'body': {'error': {'code': code}}}
    task_id = path.split('/')[4]
    if (not isinstance(body, dict) or set(body) != {'text', 'question_key', 'request_id'}
            or not isinstance(body['text'], str) or not 1 <= len(body['text'].strip()) <= 2000
            or not isinstance(body['question_key'], str) or not re.fullmatch('[a-f0-9]{64}', body['question_key'])
            or not isinstance(body['request_id'], str) or not re.fullmatch('[a-f0-9-]{36}', body['request_id'])):
        return {'http': 400, 'body': {'error': {'code': 'invalid_argument'}}}
    key = body['question_key']
    # One attempt per question signature. Identical later questions fail closed
    # until the phone exposes an atomic question-generation token.
    with state(root) as data:
        if data.get('ai') != task_id or data.get('manual') or process_identity(data.get('guard_pid')) != data.get('guard_identity') or not data.get('guard_identity'):
            return refused('control_busy_or_guard_unavailable')
        try:
            lease_info(data, info)
        except ValueError:
            return refused('conflict')
        reservation = lease_key(data)
        attempts = data.setdefault('answer_attempts', {})
        if key in attempts or len(attempts) >= 20:
            return refused('answer_already_attempted')
        attempts[key] = body['request_id']
    # Reservation is durable before any write to BK. No automatic retry, even
    # if the read or POST response disappears. Credentials and text stay out of state.
    with state(root) as data:
        if data.get('manual') or data.get('ai') != task_id:
            return refused('manual_takeover')
        if lease_key(data) != reservation:
            return refused('conflict')
        current = api(info, 'GET', '/api/v1/tasks/' + task_id)
        value = current.get('body', {})
        if current['http'] != 200 or value.get('task_id') != task_id or question_key(value) != key:
            return refused('question_changed')
        return api(info, 'POST', path, {'text': body['text']})


def activate_guard(root):
    pid = os.getpid()
    identity = process_identity(pid)
    with state(root) as data:
        data.update(guard_pid=pid, guard_identity=identity,
                    guard_protocol={'version': 2, 'pid': pid, 'identity': identity}, manual=[])


def enter_manual(root, info=None, socket_fd=None):
    claim = str(uuid.uuid4())
    owner = dict(id=claim, **manual_owner(socket_fd)) if socket_fd is not None else claim
    with state(root) as data:
        task_id = data.get('ai')
        execution = data.get('ai_execution')
        if task_id:
            try:
                info = lease_info(data, info)
            except ValueError as error:
                raise RuntimeError('Task package is unconfirmed; input blocked') from error
        reservation = lease_key(data)
        data['manual'].append(owner)
    if not task_id:
        return claim
    try:
        # Query before cancellation; starting may not yet have a live run.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            reply = api(info, 'GET', '/api/v1/tasks/' + task_id)
            task = reply.get('body', {})
            if terminal_receipt(reply, task_id, execution):
                with state(root) as data:
                    if not has_manual(data, claim):
                        raise RuntimeError('Manual claim changed; input blocked')
                    if lease_key(data) == reservation:
                        release_ai(data)
                    elif data.get('ai') is not None:
                        raise RuntimeError('Task changed; input blocked')
                    # Another confirmed query can release this completed task
                    # while our live manual claim still excludes a replacement.
                return claim
            if reply['http'] == 200 and isinstance(task, dict) and task.get('task_id') == task_id:
                with state(root) as data:
                    if not has_manual(data, claim):
                        raise RuntimeError('Manual claim changed; input blocked')
                    if lease_key(data) == reservation:
                        api(info, 'POST', '/api/v1/tasks/' + task_id + '/cancel', {})
                    elif data.get('ai') is not None:
                        raise RuntimeError('Task changed; input blocked')
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
