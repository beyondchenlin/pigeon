"""Read-only, cursor-based replication of phone run records into the private archive."""
import base64
from functools import partial
import json
import os
import re
import time
import uuid
from pathlib import Path

from .bk_evidence_store import MAX_IMAGE

RUN_ID = re.compile(r'[0-9]{8}-[0-9]{6}-[a-z0-9]{6}')


def enrolled_since(domain):
    config = Path(os.environ.get('BK_PROCESS_DEVICES_FILE', '/user/phone-pilot/bk-process-devices.json'))
    if not config.is_file():
        return None
    stamp = json.loads(config.read_text()).get(domain)
    return stamp if type(stamp) in (int, float) and stamp > 0 else None


def prepare(db):
    db.execute('CREATE TABLE IF NOT EXISTS run_process (domain TEXT, task TEXT, run TEXT, cursor INTEGER, summary TEXT, due REAL, PRIMARY KEY(domain,task))')
    db.execute('CREATE TABLE IF NOT EXISTS run_event (domain TEXT, task TEXT, seq INTEGER, body TEXT, media INTEGER, image TEXT, PRIMARY KEY(domain,task,seq))')
    db.execute('CREATE TABLE IF NOT EXISTS process_retry (domain TEXT, task TEXT, seq INTEGER, due REAL, PRIMARY KEY(domain,task,seq))')


def call_process(domain, method, path, *, task_info):
    """Use the installed phone bridge's authenticated MCP client. Reading a run never takes its input lease."""
    from .bk_ai_transport import NATIVE_INFO_BY_PACKAGE
    from .bk_device_channel import open_device
    from .bk_native_onboard import _python_command
    match = re.fullmatch(r'/api/v1/tasks/([a-f0-9-]{36})/(?:process/([0-9]{1,9})|image/([0-9]{8}-[0-9]{6}-[a-z0-9]{6})/([0-9]{1,10})/([0-9]{1,6}))', path)
    package = next((p for p, info in NATIVE_INFO_BY_PACKAGE.items() if info == task_info), None)
    if not package or method != 'GET' or not match:
        raise ValueError('invalid_process_request')
    task, cursor, run, media, offset = match.groups()
    if str(uuid.UUID(task)) != task:
        raise ValueError('invalid_task')
    if cursor is not None:
        # Page the existing on-device NDJSON export locally to keep individual RPC payloads bounded.
        arguments = [task, cursor]
        program = """import json,os,sys,urllib.request
sys.path.insert(0,os.path.join(os.environ['USRDIR'],'modules'))
from bk_native_mcp import LocalTransport,info_for_package
transport=LocalTransport(info_for_package(sys.argv[-1]))
task, offset=sys.argv[1],int(sys.argv[2])
url=transport.url.rsplit('/',1)[0]+'/api/v1/tasks/'+task+'/export?events=jsonl'
request=urllib.request.Request(url,headers=transport.headers)
with transport.opener.open(request,timeout=5) as res:
    header=json.loads(res.readline())
    if header.get('task_id')!=task: raise ValueError('task mismatch')
    batch=[]; used=0; total=0; full=False
    for line in res:
        if not line.strip(): continue
        if total>=offset and not full:
            if len(batch)==32 or (batch and used+len(line)>175000):
                full=True
            else:
                batch.append(json.loads(line)); used+=len(line)
        total+=1
print(json.dumps(dict(header, events=batch, offset=offset, total=total),ensure_ascii=False))
"""
    else:
        arguments = [json.dumps(dict(task_id=task, run_id=run, media_id=int(media), offset=int(offset)))]
        program = ("import json,os,sys;sys.path.insert(0,os.path.join(os.environ['USRDIR'],'modules'));"
                   "from bk_native_mcp import MikaMcp,LocalTransport,info_for_package;"
                   "c=MikaMcp(LocalTransport(info_for_package(sys.argv[-1])));c.initialize();"
                   "print(json.dumps(c.call('mika_task_image',json.loads(sys.argv[1])),ensure_ascii=False))")
    device = open_device(domain, 18)
    try:
        reply = device.execute_script(_python_command('-c',[program,*arguments,package]),timeout=15)
        if reply.exitstatus or len(reply.stdout)>1024*1024:
            raise ValueError('process_read_unavailable')
        return {'http':200,'body':json.loads(reply.stdout)}
    finally:
        device.channel.close()


def read_phone(call, domain, task, path):
    reply = call(domain, 'GET', '/api/v1/tasks/' + task + path)
    data = reply.get('body') or {}
    if reply.get('http') != 200 or data.get('task_id') != task:
        raise ValueError('process_unavailable')
    return data


def sync_page(store, call, domain, task, expected_run, terminal):
    with store.connect() as db:
        prepare(db)
        row = db.execute('SELECT run,cursor FROM run_process WHERE domain=? AND task=?', (domain, task)).fetchone()
    offset = row[1] if row else 0
    page = read_phone(call, domain, task, '/process/' + str(offset))
    run = page.get('run_id')
    events = page.get('events')
    total = page.get('total')
    if (not isinstance(run, str) or not RUN_ID.fullmatch(run) or run != expected_run
            or (row and row[0] != run) or page.get('offset') != offset
            or not isinstance(events, list) or len(events) > 32 or type(total) != int
            or offset + len(events) > total or (not events and offset < total)):
        raise ValueError('invalid_page')
    summary = page.get('run') or {}
    if summary.get('id') != run:
        raise ValueError('invalid_summary')
    rows = []
    for seq, event in enumerate(events, offset):
        if not isinstance(event, dict) or not isinstance(event.get('type'), str) or type(event.get('atMs')) != int:
            raise ValueError('invalid_event')
        media = event.get('mediaId') if event['type'] == 'media' else None
        if media is not None and (type(media) != int or not 0 <= media <= 2147483647):
            raise ValueError('invalid_media')
        rows.append((domain, task, seq, json.dumps(event, ensure_ascii=False), media))
    cursor = offset + len(events)
    # Judgement and final usage can arrive after the first terminal receipt.
    settled = terminal and cursor == total and row and offset == cursor and summary.get('endedAtMs')
    due = time.time() + (3600 if settled else 0 if cursor < total else 10)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        added = sum(len(r[3].encode()) for r in rows)
        if added and sum(p.stat().st_size for p in store.root.iterdir() if p.is_file()) + added + 16384 > store.max_bytes:
            raise ValueError('archive_full')
        current = db.execute('SELECT cursor FROM run_process WHERE domain=? AND task=?', (domain, task)).fetchone()
        if (current[0] if current else 0) != offset:
            return
        db.executemany('INSERT INTO run_event(domain,task,seq,body,media) VALUES(?,?,?,?,?)', rows)
        db.execute('INSERT OR REPLACE INTO run_process VALUES(?,?,?,?,?,?)',
                   (domain, task, run, cursor, json.dumps(summary, ensure_ascii=False), due))


def fetch_frame(store, call, domain, task, run, media, seq, event):
    chunks = bytearray()
    size = digest = None
    while size is None or len(chunks) < size:
        offset = len(chunks)
        page = read_phone(call, domain, task, f'/image/{run}/{media}/{offset}')
        if page.get('run_id') != run or page.get('media_id') != media or page.get('offset') != offset:
            raise ValueError('image_identity_mismatch')
        if page.get('available') is False:
            return False
        if size is None:
            size, digest = page.get('size'), page.get('sha256')
        if (type(size) != int or not 0 < size <= MAX_IMAGE or size != page.get('size')
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or digest != page.get('sha256')):
            raise ValueError('image_changed')
        part = base64.b64decode(page['data'], validate=True)
        if len(part) != min(96 * 1024, size - offset):
            raise ValueError('invalid_image_chunk')
        chunks.extend(part)
    eid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'bk-process/{domain}/{task}/{run}/{media}'))
    terminal = event.get('terminal') is True or media == 2147483647
    meta = dict(evidence_id=eid, task_id=task, run_id=run, media_id=media, phase='terminal' if terminal else 'step',
                source='bk_run_record', captured_at_ms=event['atMs'], step=event.get('step'), size=size, sha256=digest,
                mime='image/jpeg', note='执行窗口最后画面' if terminal else '步骤截图')
    store.put(domain, meta, bytes(chunks))
    with store.connect() as db:
        db.execute('UPDATE run_event SET image=? WHERE domain=? AND task=? AND seq=?', (eid, domain, task, seq))
    return True


def sync_device(store, call, domain, tasks):
    from .bk_ai_transport import NATIVE_INFO_BY_PACKAGE
    readers = {task: partial(call, task_info=info) for task, _, _, info in tasks if info in NATIVE_INFO_BY_PACKAGE.values()}
    if not readers:
        return []
    with store.connect() as db:
        prepare(db)
        due = dict(db.execute('SELECT task,due FROM run_process WHERE domain=?', (domain,)))
        due.update(db.execute('SELECT task,due FROM process_retry WHERE domain=? AND seq=-1', (domain,)))
    now = time.time()
    eligible = [t for t in tasks if t[0] in readers and due.get(t[0], 0) <= now]
    # Give live tasks first access, then rotate through finished runs awaiting backfill.
    failures = []
    for task, run, terminal, _ in sorted(eligible, key=lambda t: (t[2], due.get(t[0], 0)))[:3]:
        try:
            sync_page(store, readers[task], domain, task, run, terminal)
            with store.connect() as db:
                db.execute('DELETE FROM process_retry WHERE domain=? AND task=? AND seq=-1', (domain, task))
        except Exception as exc:
            with store.connect() as db:
                db.execute('INSERT OR REPLACE INTO process_retry VALUES(?,?,?,?)', (domain, task, -1, now + 45))
            failures.append(exc)
    # Keep image retries independent from event cursors. A stop never discards pending frames.
    with store.connect() as db:
        pending = db.execute('SELECT e.task,r.run,e.media,e.seq,e.body FROM run_event e JOIN run_process r '
                             'ON r.domain=e.domain AND r.task=e.task LEFT JOIN process_retry retry ON retry.domain=e.domain AND retry.task=e.task AND retry.seq=e.seq '
                             'WHERE e.domain=? AND coalesce(retry.due,0)<=? AND e.media IS NOT NULL '
                             'AND e.task IN (SELECT value FROM json_each(?)) '
                             'AND (e.image IS NULL OR e.image=\'retry\') ORDER BY (e.image IS NOT NULL), '
                             "(e.media=2147483647 OR coalesce(json_extract(e.body,'$.terminal'),0)) DESC,r.rowid DESC,e.seq LIMIT 10",
                             (domain, now, json.dumps(list(readers)))).fetchall()
    for task, run, media, seq, body in pending:
        try:
            ok = fetch_frame(store, readers[task], domain, task, run, media, seq, json.loads(body))
        except Exception as exc:
            failures.append(exc)
            ok = False
        with store.connect() as db:
            if ok:
                db.execute('DELETE FROM process_retry WHERE domain=? AND task=? AND seq=?', (domain, task, seq))
            else:
                db.execute("UPDATE run_event SET image='retry' WHERE domain=? AND task=? AND seq=?", (domain, task, seq))
                db.execute('INSERT OR REPLACE INTO process_retry VALUES(?,?,?,?)', (domain, task, seq, now + 60))
    if failures:
        raise failures[0]
    return []


def timeline(store, domain, task, offset):
    with store.connect() as db:
        prepare(db)
        row = db.execute('SELECT run,cursor,summary FROM run_process WHERE domain=? AND task=?', (domain,task)).fetchone()
        if not row:
            return None
        events = db.execute('SELECT seq,body,image FROM run_event WHERE domain=? AND task=? AND seq>=? ORDER BY seq LIMIT 100',
                            (domain,task,offset)).fetchall()
        return dict(run_id=row[0], total=row[1], summary=json.loads(row[2]),
                    events=[dict(seq=n, event=json.loads(body), image=None if image == 'retry' else image) for n,body,image in events])
