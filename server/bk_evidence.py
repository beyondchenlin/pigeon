"""Automatic evidence sync, deliberately independent of terminal task polling."""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from executor.models import session_scope, Device
from server.service import BaseHttpService
from .bk_ai_store import AiTask
from .bk_ai_transport import call
from .bk_evidence_store import Archive, collect, terminal_binding, DOMAIN, ID

POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='bk-evidence')
INFLIGHT = set()
NEXT = {}
HEALTH = {}
WAKEUPS = set()
SEEN_TERMINALS = OrderedDict()
SCHEDULE_LOCK = threading.Lock()
MAX_TERMINAL_NOTIFICATIONS = 4096


def notify_terminal(domain, task_id):
    """Wake evidence sync once, only after the caller commits a true terminal receipt.

    Receipt callbacks can run in worker threads. A wakeup arriving while an old
    read is in flight survives that read's success or failure; it is consumed
    only when poll actually schedules the next read. This never contacts a
    phone, changes a task, or acknowledges evidence.
    """
    if (not isinstance(domain, str) or not DOMAIN.fullmatch(domain)
            or not isinstance(task_id, str) or not ID.fullmatch(task_id)):
        return False
    key = (domain, task_id)
    with SCHEDULE_LOCK:
        if key in SEEN_TERMINALS:
            return False
        SEEN_TERMINALS[key] = None
        while len(SEEN_TERMINALS) > MAX_TERMINAL_NOTIFICATIONS:
            SEEN_TERMINALS.popitem(last=False)
        WAKEUPS.add(domain)
    return True


def archive():
    return Archive(os.environ.get('BK_EVIDENCE_DIR', '/user/phone-pilot/evidence'))


def sync(domain):
    with session_scope() as s:
        tasks = {row.id: (bool(row.execution), row.snapshot) for row in s.query(AiTask)
                 .join(Device, Device.id == AiTask.device_id).filter(Device.domain == domain)}
    def validate(meta):
        contracted, snapshot = tasks[meta['task_id']]
        return terminal_binding(snapshot, meta) if contracted else True
    return collect(domain, archive(), call, lambda task: task in tasks, validate_meta=validate)


async def perform(domain):
    with SCHEDULE_LOCK:
        INFLIGHT.add(domain)
    delay = 60
    try:
        ids = await asyncio.get_running_loop().run_in_executor(POOL, sync, domain)
        HEALTH[domain] = dict(state='connected', last_sync=time.time(), received=len(ids))
        delay = 15
    except Exception:
        # Never log native error strings or credentials. Retrying here only reads files and ACKs.
        HEALTH[domain] = dict(state='retry_pending', last_attempt=time.time())
    finally:
        with SCHEDULE_LOCK:
            NEXT[domain] = 0 if domain in WAKEUPS else time.monotonic() + delay
            INFLIGHT.discard(domain)


async def poll():
    with session_scope() as s:
        domains = [r[0] for r in s.query(Device.domain).join(AiTask, Device.id == AiTask.device_id).filter(Device.state == 'online').distinct()]
    for domain in domains:
        with SCHEDULE_LOCK:
            if len(INFLIGHT) >= 2:
                break
            due = domain in WAKEUPS or NEXT.get(domain, 0) <= time.monotonic()
            if domain in INFLIGHT or not due:
                continue
            INFLIGHT.add(domain)
            WAKEUPS.discard(domain)
        try:
            asyncio.create_task(perform(domain))
        except BaseException:
            with SCHEDULE_LOCK:
                INFLIGHT.discard(domain)
                WAKEUPS.add(domain)
            raise


class EvidenceHandler(BaseHttpService):
    def get(self, domain, eid=None):
        user, device = self.get_login_user_device(domain)
        self.set_header('Cache-Control', 'private, no-store')
        self.set_header('X-Content-Type-Options', 'nosniff')
        with session_scope() as s:
            ids = {r[0] for r in s.query(AiTask.id).filter_by(device_id=device.id, user_id=user.id)}
        store = archive()
        if eid is None:
            self.tell(dict(evidence=store.list(domain, ids), sync=HEALTH.get(domain, {'state':'waiting'})))
            return
        meta = store.get(domain, eid)
        if meta is None or meta['task_id'] not in ids or meta['size'] == 0:
            self.throw(404, 'EVIDENCE_NOT_FOUND', 'Evidence image is unavailable')
        path = store.path(domain, eid)
        if not path.is_file():
            self.throw(503, 'EVIDENCE_UNAVAILABLE', 'Archived image is unavailable')
        self.set_header('Content-Type', 'image/jpeg')
        self.set_header('Content-Security-Policy', "default-src 'none'; sandbox")
        self.write(path.read_bytes())
