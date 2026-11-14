"""Single-device BK task adapter; shares PIGEON auth, device records and DB."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import time
import uuid
import hashlib
from tornado.ioloop import PeriodicCallback
from server.service import BaseHttpService, Service
from executor.models import session_scope, engine, Device, UserDevice
from .bk_ai_store import (AiTask, AiAnswer, question_key, TERMINAL, active, locked_device,
    script_busy, change, describe, install_script_guard, ensure_schema, update_sync, request_cancel)
from .bk_ai_transport import call, mapping, TransportError, refusal_code, NATIVE_INFO
from .bk_maintenance import PUBLIC_ORIGIN
from .bk_execution import (ContractError, validate_execution, supervision_timeout,
    request_signature, supports_execution, DEFAULT_TIMEOUT_S, MIN_TIMEOUT_S, MAX_TIMEOUT_S)

POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='bk-ai')
# A held event wait can never consume the workers used to stop a phone.
EVENT_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='bk-ai-event')
CONTROL_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='bk-ai-control')
INFLIGHT = set()
EVENT_INFLIGHT = set()
CANCEL_INFLIGHT = set()
KNOWN = frozenset(('starting', 'running', 'queued', 'paused', 'waiting_for_answer',
    'waiting_on_phone', 'finishing', 'stopping', 'stop_unconfirmed', 'finish_overdue',
    'done', 'failed', 'cancelled'))


def task_call(domain, task_id, method, path, body=None):
    """Use the persisted package for follow-up operations, including after restart."""
    with session_scope() as s:
        row = s.query(AiTask).filter_by(id=task_id).one()
        info = (row.sync_meta or {}).get('phone_info')
    if info:
        return call(domain, method, path, body, task_info=info)
    return call(domain, method, path, body)


def apply_reply(task_id, reply, submitting=False, source='query'):
    terminal = _apply_reply(task_id, reply, submitting=submitting, source=source)
    if terminal is not None:
        # Notify only after the receipt transaction has committed. An in-flight
        # evidence sync may still hold the preceding running snapshot.
        try:
            from .bk_evidence import notify_terminal
            notify_terminal(*terminal)
        except Exception:
            # Evidence scheduling must not turn a committed terminal receipt
            # into a failed refresh/stop response. Periodic sync still retries.
            logging.exception('Unable to wake terminal evidence sync for task %s', task_id)


def _apply_reply(task_id, reply, submitting=False, source='query'):
    with session_scope() as s:
        row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
        if row.status in TERMINAL:
            return
        body = reply.get('body', {})
        if not isinstance(body, dict):
            change(row, 'unknown', 'invalid_phone_response')
            return
        if reply['http'] == 200 or (submitting and reply['http'] == 201):
            if body.get('task_id') == task_id and body.get('status') in KNOWN:
                if row.execution:
                    revision, sampled = body.get('revision'), body.get('sampled_at_ms')
                    if (body.get('execution') != row.execution or type(revision) is not int or revision < 0
                            or type(sampled) is not int or sampled < 0):
                        request_cancel(row, 'execution_response_mismatch')
                        change(row, 'unknown', 'execution_response_mismatch')
                        return
                    old_revision = row.snapshot.get('revision', -1)
                    old_sampled = row.snapshot.get('sampled_at_ms', -1)
                    if revision < old_revision or (revision == old_revision and sampled < old_sampled):
                        return
                    if body['status'] in TERMINAL:
                        result = body.get('execution_result')
                        if (not isinstance(result, dict) or result.get('execution_finished') is not True
                                or type(result.get('finished_at_ms')) is not int or result['finished_at_ms'] <= 0
                                or (body['status'] == 'done' and (result.get('duration_reached') is not True
                                    or result.get('business_success') is not True))):
                            request_cancel(row, 'unconfirmed_phone_terminal')
                            change(row, 'unknown', 'unconfirmed_phone_terminal')
                            return
                change(row, body['status'], snapshot=body)
                now = time.time()
                update_sync(row, phone_received_at=now, last_contact_at=now,
                            source_event_at_ms=body.get('event_at_ms'))
                if source != 'event':
                    update_sync(row, next_progress_at=now + (row.execution or {}).get('progress_interval_s', 2))
                if row.execution and body['status'] in TERMINAL:
                    return s.get(Device, row.device_id).domain, task_id
                return
        # Only a definite refusal before task execution frees the device.
        if submitting and reply['http'] in (400, 401, 403, 409, 422, 429):
            change(row, 'rejected', refusal_code(reply))
        else:
            change(row, 'unknown', 'phone_http_' + str(reply['http']))


def transport_failed(task_id, error, attempted_at):
    with session_scope() as s:
        row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
        # A late failed request must not invalidate a newer successful reply.
        if row.status not in TERMINAL and (row.sync_meta or {}).get('phone_received_at', 0) <= attempted_at:
            change(row, 'unknown', error)


def cancel_remote(task_id):
    attempted_at = time.time()
    with session_scope() as s:
        row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
        if row.status in TERMINAL or not row.cancel_requested:
            return
        meta = row.sync_meta or {}
        if meta.get('admitted') and not meta.get('dispatch_started_at'):
            change(row, 'cancelled', snapshot={'task_id': task_id, 'status': 'cancelled',
                'reason': 'cancelled_before_dispatch', 'source': 'server_receipt'})
            return
        domain = s.get(Device, row.device_id).domain
        update_sync(row, next_cancel_at=attempted_at + 2)
    try:
        # Do not wait for an ordinary poll or a held event wait before cancellation.
        task_call(domain, task_id, 'POST', '/api/v1/tasks/' + task_id + '/cancel', {})
        apply_reply(task_id, task_call(domain, task_id, 'GET', '/api/v1/tasks/' + task_id))
    except TransportError as e:
        transport_failed(task_id, e.code, attempted_at)


def scheduled_admission_error(session, row):
    if not (row.sync_meta or {}).get('job_id'):
        return None
    device = session.query(Device).filter_by(id=row.device_id).with_for_update().one()
    ownership = (session.query(UserDevice.id).filter_by(user_id=row.user_id, device_id=row.device_id)
                 .with_for_update().first())
    if device.is_deleted or not ownership:
        return 'access_revoked_before_dispatch'
    if device.state != 'online':
        return 'device_offline_before_dispatch'
    return None


def reconcile(task_id, submit=False):
    attempted_at = time.time()
    with session_scope() as s:
        row = s.query(AiTask).filter_by(id=task_id).with_for_update().one_or_none()
        if row is None or row.status in TERMINAL:
            return
        domain = s.get(Device, row.device_id).domain
        goal, execution = row.goal, row.execution
        if time.time() >= row.deadline:
            request_cancel(row, 'supervision_expired')
        update_sync(row, next_progress_at=attempted_at + (execution or {}).get('progress_interval_s', 2))
    try:
        if submit:
            with session_scope() as s:
                row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
                if row.status in TERMINAL:
                    return
                if (row.sync_meta or {}).get('dispatch_started_at'):
                    return
                if row.cancel_requested and not (row.sync_meta or {}).get('dispatch_started_at'):
                    change(row, 'cancelled', snapshot={'task_id': task_id, 'status': 'cancelled',
                        'reason': 'cancelled_before_dispatch', 'source': 'server_receipt'})
                    return
                if row.cancel_requested:
                    return
                admission_error = scheduled_admission_error(s, row)
                if admission_error:
                    change(row, 'rejected', admission_error)
                    return
            if mapping(domain).get('info') == 'auto':
                discovery = call(domain, 'GET', '/api/v1/native-discovery', discover_native=True)
                body = discovery.get('body')
                ready = discovery.get('http') == 200 and isinstance(body, dict) and body.get('state') == 'connected'
                with session_scope() as s:
                    row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
                    if row.status in TERMINAL or row.cancel_requested or (row.sync_meta or {}).get('dispatch_started_at'):
                        return
                    if not ready:
                        change(row, 'rejected', 'native_guard_or_connection_unavailable')
                        return
                    update_sync(row, phone_info=NATIVE_INFO)
                    if execution:
                        change(row, 'rejected', 'unsupported_execution_contract')
                        return
            if execution:
                capabilities = call(domain, 'GET', '/api/v1/capabilities')
                if not supports_execution(capabilities):
                    code = capabilities.get('http')
                    reason = ('unsupported_execution_contract' if code in (200, 400, 404, 422)
                              else 'capability_auth_refused' if code in (401, 403)
                              else 'capability_check_unavailable')
                    with session_scope() as s:
                        row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
                        if row.status not in TERMINAL:
                            change(row, 'rejected', reason)
                    return
            with session_scope() as s:
                row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
                if row.status in TERMINAL or (row.sync_meta or {}).get('dispatch_started_at'):
                    return
                if row.cancel_requested:
                    change(row, 'cancelled', snapshot={'task_id': task_id, 'status': 'cancelled',
                        'reason': 'cancelled_before_dispatch', 'source': 'server_receipt'})
                    return
                admission_error = scheduled_admission_error(s, row)
                if admission_error:
                    change(row, 'rejected', admission_error)
                    return
                update_sync(row, dispatch_started_at=time.time())
                native_selected = (row.sync_meta or {}).get('phone_info') == NATIVE_INFO
            payload = dict(goal=goal, request_id=task_id)
            if execution:
                payload['execution'] = execution
            reply = (call(domain, 'POST', '/api/v1/tasks', payload, create_native=True)
                     if native_selected else call(domain, 'POST', '/api/v1/tasks', payload))
            apply_reply(task_id, reply, submitting=True)
        else:
            reply = task_call(domain, task_id, 'GET', '/api/v1/tasks/' + task_id)
            apply_reply(task_id, reply)
        # Re-read intent: stop may have arrived while the query was in flight.
        with session_scope() as s:
            row = s.get(AiTask, task_id)
            cancel = row.cancel_requested and row.status not in TERMINAL
        if cancel:
            cancel_remote(task_id)
    except TransportError as e:
        transport_failed(task_id, e.code, attempted_at)


async def perform(task_id, submit=False):
    if task_id in INFLIGHT:
        return
    INFLIGHT.add(task_id)
    try:
        await asyncio.get_running_loop().run_in_executor(POOL, reconcile, task_id, submit)
    finally:
        INFLIGHT.discard(task_id)


async def poll():
    with session_scope() as s:
        now = time.time()
        rows = s.query(AiTask).filter(AiTask.status.notin_(TERMINAL)).order_by(AiTask.updated)
        ids = [(r.id, bool((r.sync_meta or {}).get('admitted')
                           and not (r.sync_meta or {}).get('dispatch_started_at')))
               for r in rows if (not r.execution
               or (r.sync_meta or {}).get('next_progress_at', 0) <= now)][:4]
    # An admitted receipt with no dispatch marker is safe to send once after a
    # crash. Once the marker exists, only query the original phone receipt.
    await asyncio.gather(*(perform(task_id, submit=submit) for task_id, submit in ids))


async def perform_cancel(task_id):
    if task_id in CANCEL_INFLIGHT:
        return
    CANCEL_INFLIGHT.add(task_id)
    try:
        await asyncio.get_running_loop().run_in_executor(CONTROL_POOL, cancel_remote, task_id)
    finally:
        CANCEL_INFLIGHT.discard(task_id)


def wait_event(task_id):
    attempted_at = time.time()
    with session_scope() as s:
        row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
        if row.status in TERMINAL or not row.execution:
            return
        meta = row.sync_meta or {}
        if meta.get('next_event_at', 0) > attempted_at:
            return
        domain = s.get(Device, row.device_id).domain
        revision = row.snapshot.get('revision')
        if row.status == 'unknown':
            revision = None  # Reconnect reconciles the original receipt, never re-submits.
        wait_ms = min(20000, max(1000, row.execution['event_latency_s'] * 1000))
        retry_s = min(5, row.execution['event_latency_s'])
        # Bounded persisted lease also prevents duplicate waiters across workers.
        update_sync(row, next_event_at=attempted_at + wait_ms / 1000 + 12)
    try:
        if type(revision) is not int:
            reply = task_call(domain, task_id, 'GET', '/api/v1/tasks/' + task_id)
        else:
            reply = task_call(domain, task_id, 'POST', '/api/v1/tasks/' + task_id + '/wait',
                         {'after_revision': revision, 'timeout_ms': wait_ms})
        if reply['http'] == 204:
            with session_scope() as s:
                row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
                update_sync(row, last_contact_at=time.time())
        else:
            apply_reply(task_id, reply, source='event')
        backoff = 0 if reply['http'] in (200, 204) else retry_s
    except TransportError as e:
        transport_failed(task_id, e.code, attempted_at)
        backoff = retry_s
    finally:
        with session_scope() as s:
            row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
            update_sync(row, next_event_at=time.time() + locals().get('backoff', retry_s))


async def perform_event(task_id):
    try:
        await asyncio.get_running_loop().run_in_executor(EVENT_POOL, wait_event, task_id)
    finally:
        EVENT_INFLIGHT.discard(task_id)


async def event_poll():
    """Critical events and stop intent run independently from summary polling."""
    now = time.time()
    with session_scope() as s:
        rows = s.query(AiTask).filter(AiTask.status.notin_(TERMINAL)).order_by(AiTask.updated).with_for_update()
        events, cancellations = [], []
        for row in rows:
            if now >= row.deadline:
                request_cancel(row, 'supervision_expired')
            meta = row.sync_meta or {}
            if row.cancel_requested and meta.get('next_cancel_at', 0) <= now:
                cancellations.append(row.id)
            if row.execution and meta.get('next_event_at', 0) <= now:
                events.append(row.id)
    available = max(0, 2 - len(CANCEL_INFLIGHT))
    for task_id in [value for value in cancellations if value not in CANCEL_INFLIGHT][:available]:
        asyncio.create_task(perform_cancel(task_id))
    for task_id in events:
        if len(EVENT_INFLIGHT) >= 4:
            break
        if task_id not in EVENT_INFLIGHT:
            EVENT_INFLIGHT.add(task_id)
            asyncio.create_task(perform_event(task_id))


def deliver_answer(domain, task_id, request_id, key, text):
    # The durable attempt already exists. A restart/lost response never replays it.
    status = 'unknown'
    try:
        reply = task_call(domain, task_id, 'POST', '/api/v1/tasks/' + task_id + '/answer',
                     dict(text=text, question_key=key, request_id=request_id))
        body = reply.get('body', {})
        if reply['http'] == 200 and isinstance(body, dict) and body.get('accepted') is True and body.get('task_id') == task_id:
            status = 'accepted'
        elif reply['http'] in (400, 401, 403, 404, 409, 422):
            status = 'rejected'
    except TransportError:
        pass
    with session_scope() as s:
        s.get(AiAnswer, request_id).status = status
    reconcile(task_id)


class NativeDiscoveryHandler(BaseHttpService):
    async def get(self, domain):
        self.set_header('Cache-Control', 'no-store')
        _, device = self.get_login_user_device(domain)
        if device.state != 'online':
            self.tell({'state': 'offline'})
            return
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                POOL, lambda: call(domain, 'GET', '/api/v1/native-discovery', discover_native=True))
            body = result.get('body')
            if result.get('http') != 200 or not isinstance(body, dict):
                body = {'state': 'not_ready'}
            allowed = {'connected', 'not_installed', 'not_ready', 'auth_failed', 'incompatible', 'guard_not_ready'}
            raw_state = body.get('state')
            state = raw_state if isinstance(raw_state, str) and raw_state in allowed else 'not_ready'
            value = {'state': state}
            if state in ('connected', 'guard_not_ready') and isinstance(body.get('version'), str):
                value['version'] = body['version'][:80]
            self.tell(value)
        except TransportError:
            self.tell({'state': 'not_ready'})


class AiHandler(BaseHttpService):
    def prepare(self):
        self.set_header('Cache-Control', 'no-store')
        if self.request.method == 'POST':
            origin = self.request.headers.get('Origin')
            if origin:
                allowed = {self.request.protocol + '://' + self.request.host, os.environ.get('BK_AI_ORIGIN') or PUBLIC_ORIGIN}
                if origin not in allowed or self.request.headers.get('X-Requested-With') != 'BK-Task':
                    logging.getLogger(__name__).warning('BK task origin refused: origin=%r allowed=%r explicit=%s',
                        origin[:200], sorted(allowed), self.request.headers.get('X-Requested-With') == 'BK-Task')
                    self.throw(403, 'AI_ORIGIN', 'Use the same-origin task page or an authenticated API client')
            elif self.get_cookie('token') and self.request.headers.get('X-Requested-With') != 'BK-Task':
                self.throw(403, 'AI_REQUEST', 'Explicit task request required')

    def body(self):
        if len(self.request.body) > 20000:
            self.throw(413, 'AI_BODY', 'Request too large')
        try:
            value = json.loads(self.request.body)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            self.throw(400, 'AI_BODY', 'Expected a JSON object')

    def lookup(self, domain, task_id):
        user, device = self.get_login_user_device(domain)
        with session_scope() as s:
            row = s.query(AiTask).filter_by(id=task_id, device_id=device.id, user_id=user.id).first()
            if row is None:
                self.throw(404, 'AI_NOT_FOUND', 'No such task for this device')
            return describe(row, s)

    async def get(self, domain, task_id=None, action=None):
        user, device = self.get_login_user_device(domain)
        if action:
            self.throw(405, 'AI_METHOD', 'POST required')
        if task_id:
            current = self.lookup(domain, task_id)
            refresh = self.get_query_argument('refresh', None)
            if refresh not in (None, '0', '1'):
                self.throw(400, 'AI_REFRESH', 'refresh must be 0 or 1')
            if refresh == '1' or (refresh is None and not current.get('execution')):
                await perform(task_id)
            self.tell(self.lookup(domain, task_id))
        else:
            with session_scope() as s:
                rows = s.query(AiTask).filter_by(device_id=device.id, user_id=user.id).order_by(AiTask.created.desc()).limit(50)
                self.tell({'tasks': [describe(r, s) for r in rows]})

    async def post(self, domain, task_id=None, action=None):
        user, device = self.get_login_user_device(domain)
        body = self.body()
        if task_id:
            self.lookup(domain, task_id)
            if action == 'answer':
                await self.answer(domain, device, task_id, body)
                return
            if action not in ('cancel', 'takeover') or body:
                self.throw(400, 'AI_ACTION', 'Use cancel or takeover with an empty object')
            with session_scope() as s:
                row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
                if row.status not in TERMINAL:
                    request_cancel(row)
            await perform_cancel(task_id)
            result = self.lookup(domain, task_id)
            self.set_status(200 if result['status'] in TERMINAL else 202)
            self.tell(result)
            return
        if set(body) - {'goal', 'request_id', 'timeout_s', 'execution'}:
            self.throw(400, 'AI_FIELDS', 'Unsupported fields')
        goal, request_id = body.get('goal'), body.get('request_id')
        try:
            execution = validate_execution(body['execution']) if 'execution' in body else None
        except ContractError as e:
            self.throw(400, 'AI_EXECUTION', str(e))
        try:
            timeout = supervision_timeout(execution, body.get('timeout_s'), 'timeout_s' in body)
        except ContractError as e:
            self.throw(400, 'AI_TIMEOUT', str(e))
        try:
            valid_id = str(uuid.UUID(request_id)) == request_id
        except (ValueError, TypeError, AttributeError):
            valid_id = False
        if not valid_id or not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 4000:
            self.throw(400, 'AI_ARGUMENT', 'goal and a canonical UUID request_id are required')
        goal = goal.strip()
        try:
            mapping(domain)
        except TransportError as e:
            self.throw(503, 'AI_CONFIG', e.code)
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'pigeon-bk:' + domain + ':' + request_id))
        with session_scope() as s:
            current = locked_device(s, device.id)
            old = s.query(AiTask).filter_by(device_id=device.id, request_id=request_id).first()
            if old:
                if old.user_id != user.id:
                    self.throw(409, 'AI_REQUEST_REUSED', 'request_id is already in use')
                if request_signature(old.goal, int(round(old.deadline - old.created)), old.execution) != request_signature(goal, timeout, execution):
                    self.throw(409, 'AI_REQUEST_REUSED', 'request_id already belongs to a different request')
                self.tell(describe(old, s))
                return
            if current.state != 'online':
                self.throw(503, 'AI_OFFLINE', 'Device is offline; nothing was submitted')
            if current.controlling or script_busy(s, device.id) or active(s, device.id):
                self.throw(409, 'AI_BUSY', 'Device has an active controller, script or unresolved AI task')
            now = time.time()
            row = AiTask(id=task_id, device_id=device.id, user_id=user.id, request_id=request_id,
                goal=goal, status='submitting', created=now, updated=now, deadline=now + timeout,
                history=[{'at': now, 'status': 'submitting', 'error': None}], snapshot={},
                execution=execution, sync_meta={'admitted': True})
            s.add(row)
        # Durable receipt precedes the only creation attempt. Restart only polls.
        await perform(task_id, submit=True)
        self.set_status(202)
        self.tell(self.lookup(domain, task_id))

    async def answer(self, domain, device, task_id, body):
        text, key, request_id = body.get('text'), body.get('question_key'), body.get('request_id')
        try:
            valid_id = str(uuid.UUID(request_id)) == request_id
        except (ValueError, TypeError, AttributeError):
            valid_id = False
        if (set(body) != {'text', 'question_key', 'request_id'} or not valid_id
                or not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000
                or not isinstance(key, str) or len(key) != 64):
            self.throw(400, 'AI_ANSWER_ARGUMENT', 'A question key, reply text and canonical request UUID are required')
        text = text.strip()
        digest = hashlib.sha256(text.encode()).hexdigest()
        with session_scope() as s:
            current = locked_device(s, device.id)
            row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
            old = s.get(AiAnswer, request_id)
            if old:
                if (old.task_id, old.question_key, old.text_sha256) != (task_id, key, digest):
                    self.throw(409, 'AI_ANSWER_REUSED', 'Reply ID already belongs to another answer')
                self.tell(describe(row, s))
                return
            if (row.status != 'waiting_for_answer' or question_key(row.snapshot) != key
                    or row.cancel_requested or time.time() >= row.deadline):
                self.throw(409, 'AI_QUESTION_CLOSED', 'Refresh this task; the question is no longer answerable')
            if s.query(AiAnswer).filter_by(task_id=task_id, question_key=key).first():
                self.throw(409, 'AI_ANSWER_ATTEMPTED', 'This question already has a reply attempt; do not resend')
            if task_id in INFLIGHT or current.controlling or current.state != 'online':
                self.throw(409, 'AI_ANSWER_BUSY', 'Wait for the current task status check or controller to finish')
            s.add(AiAnswer(id=request_id, task_id=task_id, question_key=key, text_sha256=digest, status='attempted'))
        INFLIGHT.add(task_id)
        try:
            await asyncio.get_running_loop().run_in_executor(POOL, deliver_answer, domain, task_id, request_id, key, text)
        finally:
            INFLIGHT.discard(task_id)
        self.tell(self.lookup(domain, task_id))


def start_with_polling(http, original_start, **settings):
    # Tornado's asyncio implementation overrides IOLoop.start. Hook the actual
    # implementation only during this synchronous server startup, and schedule
    # on the manager's own loop (the compiled server creates it internally).
    from tornado.platform.asyncio import BaseAsyncIOLoop
    original_run = BaseAsyncIOLoop.start
    def run(loop):
        if getattr(http, 'ioloop', None) is loop:
            BaseAsyncIOLoop.start = original_run
            def begin():
                callback = PeriodicCallback(poll, 2000)
                callback.start()
                http._bk_ai_poll = callback
                event_callback = PeriodicCallback(event_poll, 500)
                event_callback.start()
                http._bk_ai_event_poll = event_callback
                from . import bk_evidence
                evidence_callback = PeriodicCallback(bk_evidence.poll, 5000)
                evidence_callback.start()
                http._bk_evidence_poll = evidence_callback
                from . import bk_fleet
                fleet_callback = PeriodicCallback(bk_fleet.poll, 2000)
                fleet_callback.start()
                http._bk_fleet_poll = fleet_callback
            loop.add_callback(begin)
        return original_run(loop)
    BaseAsyncIOLoop.start = run
    try:
        return original_start(http, **settings)
    finally:
        BaseAsyncIOLoop.start = original_run


def install():
    if getattr(Service, '_bk_ai_installed', False):
        return
    ensure_schema(engine)
    AiAnswer.__table__.create(engine, checkfirst=True)
    from .bk_fleet_store import FleetDraft
    FleetDraft.__table__.create(engine, checkfirst=True)
    from .bk_fleet import install_legacy_action_guards
    install_legacy_action_guards()
    install_script_guard()
    original = Service.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.http.add_handler(r'/api/v1/device/([a-z0-9]+)/ai/native-discovery',
                              'server.bk_ai', handler='NativeDiscoveryHandler')
        self.http.add_handler(r'/api/v1/device/([a-z0-9]+)/ai/tasks(?:/([a-f0-9-]{36})(?:/(cancel|takeover|answer))?)?',
                              'server.bk_ai', handler='AiHandler')
        self.http.add_handler(r'/api/v1/device/([a-z0-9]+)/ai/evidence(?:/([a-f0-9-]{36})/image)?',
                              'server.bk_evidence', handler='EvidenceHandler')
        self.http.add_handler(r'/api/v1/ai/plan/preview',
                              'server.bk_fleet', handler='FleetPlanPreviewHandler')
        self.http.add_handler(r'/api/v1/ai/chat/plan',
                              'server.bk_fleet', handler='FleetChatPlanHandler')
        self.http.add_handler(r'/api/v1/ai/chat/drafts/([a-f0-9-]{36})',
                              'server.bk_fleet', handler='FleetDraftHandler')
        self.http.add_handler(r'/api/v1/ai/models',
                              'server.bk_fleet', handler='FleetModelListHandler')
        self.http.add_handler(r'/api/v1/ai/jobs(?:/([0-9]+)(?:/(cancel))?)?',
                              'server.bk_fleet', handler='FleetJobHandler')
    Service.__init__ = initialize
    original_start = __import__('server.service', fromlist=['HttpServiceManager']).HttpServiceManager.start_server
    def start(http, **settings):
        return start_with_polling(http, original_start, **settings)
    __import__('server.service', fromlist=['HttpServiceManager']).HttpServiceManager.start_server = start
    Service._bk_ai_installed = True
