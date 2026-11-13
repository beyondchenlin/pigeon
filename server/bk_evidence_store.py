"""Private, bounded evidence archive. Importing this module never connects to a phone."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager

ID = re.compile(r'[a-f0-9-]{36}')
DOMAIN = re.compile(r'[a-z0-9]+')
MAX_IMAGE = 768 * 1024
CHUNK = 128 * 1024


class Archive:
    def __init__(self, root, max_bytes=2 * 1024**3):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS evidence (domain TEXT, id TEXT, task TEXT, meta TEXT, archived REAL, PRIMARY KEY(domain,id))')
            db.execute('CREATE INDEX IF NOT EXISTS evidence_task ON evidence(domain, task)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.root / 'index.sqlite'), timeout=15)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def path(self, domain, eid):
        if not DOMAIN.fullmatch(domain) or not ID.fullmatch(eid):
            raise ValueError('invalid_identity')
        return self.root / (domain + '-' + eid + '.jpg')

    def get(self, domain, eid):
        self.path(domain, eid)
        with self.connect() as db:
            row = db.execute('SELECT meta FROM evidence WHERE domain=? AND id=?', (domain,eid)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, domain, task_ids):
        if not task_ids:
            return []
        # Task ownership is supplied by the authenticated API, not by the phone or browser.
        with self.connect() as db:
            rows = db.execute('SELECT meta FROM evidence WHERE domain=? ORDER BY archived DESC', (domain,))
            result = []
            for row in rows:
                meta = json.loads(row[0])
                if meta['task_id'] in task_ids:
                    result.append(meta)
                    if len(result) == 100:
                        break
        return result

    def put(self, domain, meta, data):
        eid = meta['evidence_id']
        path = self.path(domain, eid)
        if not ID.fullmatch(meta['task_id']) or type(meta['size']) is not int or not 0 <= meta['size'] <= MAX_IMAGE:
            raise ValueError('invalid_manifest')
        if len(json.dumps(meta).encode()) > 8192 or meta['mime'] != 'image/jpeg':
            raise ValueError('invalid_manifest')
        if len(data) != meta['size'] or (hashlib.sha256(data).hexdigest() if data else '') != meta['sha256']:
            raise ValueError('integrity_failed')
        if data and (not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9')):
            raise ValueError('not_jpeg')
        value = dict(meta, device_domain=domain, sync_state='archived', archived_at=time.time())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT meta FROM evidence WHERE domain=? AND id=?', (domain,eid)).fetchone()
            if old:
                previous = json.loads(old[0])
                mutable = {'sync_state', 'archived_at', 'device_domain'}
                if ({k: v for k, v in previous.items() if k not in mutable} !=
                        {k: v for k, v in meta.items() if k not in mutable}):
                    raise ValueError('evidence_identity_reused')
                if data and (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != meta['sha256']):
                    raise ValueError('archive_corrupt')
                return previous
            if sum(p.stat().st_size for p in self.root.iterdir() if p.is_file()) + len(data) + 16384 > self.max_bytes:
                raise ValueError('archive_full')
            if data:
                temporary = path.with_suffix('.part')
                with temporary.open('wb') as f:
                    os.chmod(temporary, 0o600)
                    f.write(data); f.flush(); os.fsync(f.fileno())
                os.replace(temporary, path)
                # Durability of the rename before committing the index / ACK.
                if os.name != 'nt':
                    fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try: os.fsync(fd)
                    finally: os.close(fd)
            db.execute('INSERT INTO evidence VALUES (?,?,?,?,?)', (domain,eid,meta['task_id'],json.dumps(value),time.time()))
        return value


def terminal_binding(snapshot, meta):
    """A new browse result is bound to the evidence captured by that exact run."""
    result = snapshot.get('execution_result') or {}
    expected = result.get('evidence') or {}
    if result.get('execution_finished') is not True or not expected:
        return False  # Keep it on the phone until its task receipt is reconciled.
    state = expected.get('state')
    if state not in ('captured', 'missing') or meta.get('task_id') != snapshot.get('task_id'):
        raise ValueError('unexpected_terminal_evidence')
    for field in ('evidence_id', 'task_id', 'run_id', 'display_id', 'captured_at_ms', 'source'):
        if (state == 'captured' and field not in expected) or meta.get(field) != expected.get(field):
            raise ValueError('terminal_evidence_binding_mismatch')
    if state == 'missing':
        if meta.get('state') != 'missing' or meta.get('size') != 0 or meta.get('sha256') != '' or not meta.get('reason'):
            raise ValueError('invalid_missing_evidence')
        return True  # Archive the explicit missing record; never leave it blocking later captures.
    started, finished, captured = (result.get('actual_started_at_ms'), result.get('finished_at_ms'), meta.get('captured_at_ms'))
    if (meta.get('phase') != 'result' or meta.get('source') != 'bk_task_display'
            or meta.get('capture_timing') != 'terminal_before_release' or meta.get('late_capture') is not False
            or type(meta.get('display_id')) is not int or meta['display_id'] < 0
            or type(meta.get('captured_at_ms')) is not int or meta['captured_at_ms'] <= 0
            or type(started) is not int or type(finished) is not int or not started <= captured <= finished
            or not isinstance(meta.get('run_id'), str) or not meta['run_id']
            or snapshot.get('run_id') != meta['run_id']
            or not isinstance(meta.get('android_id'), str) or not meta['android_id']):
        raise ValueError('invalid_terminal_evidence_provenance')
    return True


def collect(domain, archive, call, owns_task, limit=4, validate_meta=None):
    """At-least-once evidence transfer; this function has no task submission API."""
    reply = call(domain, 'GET', '/api/v1/evidence')
    if reply['http'] != 200:
        raise ValueError('manifest_unavailable')
    records = reply['body']['evidence']
    if not isinstance(records, list) or len(records) > 16:
        raise ValueError('invalid_manifest')
    saved = []
    for meta in records[:limit]:
        eid, size = meta['evidence_id'], meta['size']
        archive.path(domain, eid)
        if type(size) is not int or not 0 <= size <= MAX_IMAGE or not owns_task(meta['task_id']):
            raise ValueError('unowned_or_invalid_evidence')
        if validate_meta is not None and not validate_meta(meta):
            continue
        data = bytearray()
        for offset in range(0, size, CHUNK):
            import base64
            chunk = call(domain, 'GET', '/api/v1/evidence/' + eid + '/chunk/' + str(offset))
            b = chunk.get('body', {})
            if chunk['http'] != 200 or b.get('evidence_id') != eid or b.get('offset') != offset or b.get('size') != size or b.get('sha256') != meta['sha256']:
                raise ValueError('invalid_chunk')
            raw = base64.b64decode(b['data'], validate=True)
            if len(raw) != min(CHUNK, size-offset):
                raise ValueError('invalid_chunk_size')
            data.extend(raw)
        archive.put(domain, meta, bytes(data))
        ack = call(domain, 'POST', '/api/v1/evidence/' + eid + '/ack', {'sha256':meta['sha256']})
        if ack['http'] != 200 or ack['body'].get('archived') is not True:
            raise ValueError('ack_unconfirmed')
        saved.append(eid)
    return saved