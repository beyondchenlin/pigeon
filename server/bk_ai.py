"""Single-device BK task adapter; shares PIGEON auth, device records and DB."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time
import uuid
from tornado.ioloop import PeriodicCallback
from server.service import BaseHttpService, Service
from executor.models import session_scope, engine, Device
from .bk_ai_store import AiTask, TERMINAL, active, locked_device, script_busy, change, describe, install_script_guard
from .bk_ai_transport import call, mapping, TransportError

POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='bk-ai')
INFLIGHT = set()
KNOWN = frozenset(('starting', 'running', 'queued', 'paused', 'waiting_for_answer', 'waiting_on_phone', 'done', 'failed', 'cancelled'))


def apply_reply(task_id, reply, submitting=False):
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
                change(row, body['status'], snapshot=body)
                return
        # Only a definite refusal before task execution frees the device.
        if submitting and reply['http'] in (400, 401, 403, 409, 422, 429):
            change(row, 'rejected', 'phone_refused_' + str(reply['http']))
        else:
            change(row, 'unknown', 'phone_http_' + str(reply['http']))


def reconcile(task_id, submit=False):
    with session_scope() as s:
        row = s.get(AiTask, task_id)
        if row is None or row.status in TERMINAL:
            return
        domain = s.get(Device, row.device_id).domain
        goal = row.goal
        cancel = row.cancel_requested or time.time() >= row.deadline
        if cancel:
            row.cancel_requested = True
    try:
        if submit:
            reply = call(domain, 'POST', '/api/v1/tasks', dict(goal=goal, request_id=task_id))
            apply_reply(task_id, reply, submitting=True)
        else:
            reply = call(domain, 'GET', '/api/v1/tasks/' + task_id)
            apply_reply(task_id, reply)
        body = reply.get('body', {})
        if cancel and isinstance(body, dict) and body.get('status') in KNOWN - TERMINAL:
            # Cancellation is state-based and safe to repeat until terminal, never creation.
            call(domain, 'POST', '/api/v1/tasks/' + task_id + '/cancel', {})
            apply_reply(task_id, call(domain, 'GET', '/api/v1/tasks/' + task_id))
    except TransportError as e:
        with session_scope() as s:
            row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
            if row.status not in TERMINAL:
                change(row, 'unknown', e.code)


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
        ids = [r.id for r in s.query(AiTask).filter(AiTask.status.notin_(TERMINAL)).order_by(AiTask.updated).limit(4)]
    await asyncio.gather(*(perform(task_id) for task_id in ids))


class AiHandler(BaseHttpService):
    def prepare(self):
        self.set_header('Cache-Control', 'no-store')
        if self.request.method == 'POST' and self.request.headers.get('Origin'):
            self.throw(403, 'AI_ORIGIN', 'Use an authenticated API client')

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
        _, device = self.get_login_user_device(domain)
        with session_scope() as s:
            row = s.query(AiTask).filter_by(id=task_id, device_id=device.id).first()
            if row is None:
                self.throw(404, 'AI_NOT_FOUND', 'No such task for this device')
            return describe(row)

    async def get(self, domain, task_id=None, action=None):
        _, device = self.get_login_user_device(domain)
        if action:
            self.throw(405, 'AI_METHOD', 'POST required')
        if task_id:
            self.lookup(domain, task_id)
            await perform(task_id)
            self.tell(self.lookup(domain, task_id))
        else:
            with session_scope() as s:
                rows = s.query(AiTask).filter_by(device_id=device.id).order_by(AiTask.created.desc()).limit(50)
                self.tell({'tasks': [describe(r) for r in rows]})

    async def post(self, domain, task_id=None, action=None):
        user, device = self.get_login_user_device(domain)
        body = self.body()
        if task_id:
            self.lookup(domain, task_id)
            if action not in ('cancel', 'takeover') or body:
                self.throw(400, 'AI_ACTION', 'Use cancel or takeover with an empty object')
            with session_scope() as s:
                row = s.query(AiTask).filter_by(id=task_id).with_for_update().one()
                if row.status not in TERMINAL:
                    row.cancel_requested = True
            await perform(task_id)
            result = self.lookup(domain, task_id)
            self.set_status(200 if result['status'] in TERMINAL else 202)
            self.tell(result)
            return
        if set(body) - {'goal', 'request_id', 'timeout_s'}:
            self.throw(400, 'AI_FIELDS', 'Unsupported fields')
        goal, request_id = body.get('goal'), body.get('request_id')
        timeout = body.get('timeout_s', 180)
        try:
            valid_id = str(uuid.UUID(request_id)) == request_id
        except (ValueError, TypeError, AttributeError):
            valid_id = False
        if not valid_id or not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 4000:
            self.throw(400, 'AI_ARGUMENT', 'goal and a canonical UUID request_id are required')
        if type(timeout) is not int or not 15 <= timeout <= 600:
            self.throw(400, 'AI_TIMEOUT', 'timeout_s must be an integer between 15 and 600')
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
                if old.goal != goal or int(round(old.deadline - old.created)) != timeout:
                    self.throw(409, 'AI_REQUEST_REUSED', 'request_id already belongs to a different request')
                self.tell(describe(old))
                return
            if current.state != 'online':
                self.throw(503, 'AI_OFFLINE', 'Device is offline; nothing was submitted')
            if current.controlling or script_busy(s, device.id) or active(s, device.id):
                self.throw(409, 'AI_BUSY', 'Device has an active controller, script or unresolved AI task')
            now = time.time()
            row = AiTask(id=task_id, device_id=device.id, user_id=user.id, request_id=request_id,
                goal=goal, status='submitting', created=now, updated=now, deadline=now + timeout,
                history=[{'at': now, 'status': 'submitting', 'error': None}], snapshot={})
            s.add(row)
        # Durable receipt precedes the only creation attempt. Restart only polls.
        await perform(task_id, submit=True)
        self.set_status(202)
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
    AiTask.__table__.create(engine, checkfirst=True)
    install_script_guard()
    original = Service.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.http.add_handler(r'/api/v1/device/([a-z0-9]+)/ai/tasks(?:/([a-f0-9-]{36})(?:/(cancel|takeover))?)?',
                              'server.bk_ai', handler='AiHandler')
    Service.__init__ = initialize
    original_start = __import__('server.service', fromlist=['HttpServiceManager']).HttpServiceManager.start_server
    def start(http, **settings):
        return start_with_polling(http, original_start, **settings)
    __import__('server.service', fromlist=['HttpServiceManager']).HttpServiceManager.start_server = start
    Service._bk_ai_installed = True
