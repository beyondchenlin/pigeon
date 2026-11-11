"""Automatic evidence sync, deliberately independent of terminal task polling."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import time
from executor.models import session_scope, Device
from server.service import BaseHttpService
from .bk_ai_store import AiTask
from .bk_ai_transport import call
from .bk_evidence_store import Archive, collect

POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='bk-evidence')
INFLIGHT = set()
NEXT = {}
HEALTH = {}


def archive():
    return Archive(os.environ.get('BK_EVIDENCE_DIR', '/user/phone-pilot/evidence'))


def sync(domain):
    with session_scope() as s:
        task_ids = {r[0] for r in s.query(AiTask.id).join(Device, Device.id == AiTask.device_id).filter(Device.domain == domain)}
    return collect(domain, archive(), call, lambda task: task in task_ids)


async def perform(domain):
    INFLIGHT.add(domain)
    try:
        ids = await asyncio.get_running_loop().run_in_executor(POOL, sync, domain)
        HEALTH[domain] = dict(state='connected', last_sync=time.time(), received=len(ids))
        NEXT[domain] = time.monotonic() + 15
    except Exception:
        # Never log native error strings or credentials. Retrying here only reads files and ACKs.
        HEALTH[domain] = dict(state='retry_pending', last_attempt=time.time())
        NEXT[domain] = time.monotonic() + 60
    finally:
        INFLIGHT.discard(domain)


async def poll():
    with session_scope() as s:
        domains = [r[0] for r in s.query(Device.domain).join(AiTask, Device.id == AiTask.device_id).filter(Device.state == 'online').distinct()]
    for domain in domains:
        if len(INFLIGHT) >= 2:
            break
        if domain not in INFLIGHT and NEXT.get(domain, 0) <= time.monotonic():
            INFLIGHT.add(domain)
            asyncio.create_task(perform(domain))


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
