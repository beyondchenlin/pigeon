import base64
import hashlib
import json
import tempfile
import unittest
import uuid
from unittest.mock import patch
from server.bk_evidence_store import Archive, collect, CHUNK, MAX_IMAGE, terminal_binding


class EvidenceTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bk-evidence-test-')
        self.addCleanup(self.temp.cleanup)
        self.store = Archive(self.temp.name)
        self.image = b'\xff\xd8' + b'x' * (CHUNK + 5) + b'\xff\xd9'
        self.meta = dict(evidence_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()), size=len(self.image),
            sha256=hashlib.sha256(self.image).hexdigest(), mime='image/jpeg', phase='result', sync_state='pending')
        self.calls = []
        self.outage = None
        self.pending = True

    def call(self, domain, method, path, body=None):
        self.calls.append((method,path))
        if path == '/api/v1/evidence':
            return dict(http=200,body={'evidence':[self.meta] if self.pending else []})
        if path.endswith('/ack'):
            if self.outage == 'ack': raise ConnectionError('lost ack')
            self.pending = False
            if self.outage == 'ack_response': raise ConnectionError('phone applied ack but response was lost')
            return dict(http=200,body={'archived':True})
        offset = int(path.rsplit('/',1)[1])
        if self.outage == 'chunk' and offset: raise ConnectionError('offline')
        return dict(http=200,body=dict(evidence_id=self.meta['evidence_id'], offset=offset, size=len(self.image),
            sha256=self.meta['sha256'], data=base64.b64encode(self.image[offset:offset+CHUNK]).decode()))

    def transfer(self):
        return collect('phonea',self.store,self.call,lambda task: task == self.meta['task_id'])

    def test_disconnect_mid_image_restarts_transfer_without_executing_business(self):
        self.outage = 'chunk'
        with self.assertRaises(ConnectionError): self.transfer()
        self.assertIsNone(self.store.get('phonea',self.meta['evidence_id']))
        self.outage = None
        self.transfer(); self.transfer()
        self.assertEqual(len(self.store.list('phonea',{self.meta['task_id']})),1)
        self.assertTrue(all(path.startswith('/api/v1/evidence') for method,path in self.calls))
        self.assertTrue(all(method == 'GET' or path.endswith('/ack') for method,path in self.calls))

    def test_commit_then_ack_loss_idempotent_after_archive_restart(self):
        self.outage = 'ack'
        with self.assertRaises(ConnectionError): self.transfer()
        self.store = Archive(self.temp.name)
        self.outage = None
        self.transfer()
        self.assertEqual(len(self.store.list('phonea',{self.meta['task_id']})),1)

    def test_lost_ack_response_keeps_archive_without_replaying_task(self):
        self.outage = 'ack_response'
        with self.assertRaises(ConnectionError): self.transfer()
        self.assertFalse(self.pending)
        self.store = Archive(self.temp.name)
        self.calls.clear()
        self.outage = None
        self.assertEqual(self.transfer(), [])
        self.assertEqual(self.calls, [('GET', '/api/v1/evidence')])
        self.assertEqual(len(self.store.list('phonea',{self.meta['task_id']})),1)
        self.assertEqual(self.store.path('phonea',self.meta['evidence_id']).read_bytes(), self.image)

    def test_bad_hash_and_unowned_task_are_not_acked(self):
        self.meta['sha256'] = '0'*64
        with self.assertRaises(ValueError): self.transfer()
        self.assertFalse(any(path.endswith('/ack') for _,path in self.calls))
        with self.assertRaises(ValueError): collect('phonea',self.store,self.call,lambda task:False)

    def test_same_id_cannot_change_content_or_task(self):
        self.store.put('phonea',self.meta,self.image)
        other = dict(self.meta, task_id=str(uuid.uuid4()))
        with self.assertRaises(ValueError): self.store.put('phonea',other,self.image)
        self.assertEqual(self.store.list('phonea', {other['task_id']}),[])
        self.assertEqual(self.store.list('phoneb', {self.meta['task_id']}),[])

    def test_retry_cannot_omit_immutable_metadata(self):
        self.meta['run_id'] = 'original-run'
        self.store.put('phonea',self.meta,self.image)
        incomplete = {k:v for k,v in self.meta.items() if k != 'run_id'}
        with self.assertRaisesRegex(ValueError, 'evidence_identity_reused'):
            self.store.put('phonea',incomplete,self.image)
        self.assertEqual(self.store.get('phonea',self.meta['evidence_id'])['run_id'], 'original-run')

    def test_quota_and_paths_fail_without_losing_evidence(self):
        self.store.max_bytes = 1
        with self.assertRaises(ValueError): self.transfer()
        self.assertTrue(self.pending)
        with self.assertRaises(ValueError): self.store.path('../escape',self.meta['evidence_id'])

    def terminal_meta(self):
        self.meta.update(run_id='original-run', display_id=2, captured_at_ms=123456,
            source='bk_task_display', capture_timing='terminal_before_release', late_capture=False,
            android_id='synthetic-device')
        expected = {key:self.meta[key] for key in ('evidence_id','task_id','run_id',
                    'display_id','captured_at_ms','source')}
        return {'task_id':self.meta['task_id'], 'run_id':'original-run', 'execution_result':{'evidence':dict(expected,state='captured'),
            'execution_finished':True, 'actual_started_at_ms':123000, 'finished_at_ms':123999}}

    def test_terminal_image_is_bound_to_task_run_display_and_capture_time(self):
        snapshot = self.terminal_meta()
        for field, value in [('task_id',str(uuid.uuid4())), ('run_id','later-run'),
                             ('display_id',0), ('captured_at_ms',123457), ('source','later_capture')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                terminal_binding(snapshot,dict(self.meta, **{field:value}))
        self.assertTrue(terminal_binding(snapshot,self.meta))
        self.transfer()
        self.assertEqual(self.store.get('phonea',self.meta['evidence_id'])['device_domain'],'phonea')

    def test_late_or_unidentified_image_cannot_be_presented_as_terminal_capture(self):
        snapshot = self.terminal_meta()
        for field, value in [('capture_timing','later'),('late_capture',True),('android_id','')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                terminal_binding(snapshot,dict(self.meta, **{field:value}))

    def test_matching_manifest_and_result_cannot_replace_the_actual_run(self):
        snapshot = self.terminal_meta()
        snapshot['run_id'] = 'actual-run'
        with self.assertRaisesRegex(ValueError, 'invalid_terminal_evidence_provenance'):
            collect('phonea',self.store,self.call,lambda task:True,
                validate_meta=lambda meta:terminal_binding(snapshot,meta))
        self.assertTrue(self.pending)
        self.assertIsNone(self.store.get('phonea',self.meta['evidence_id']))

    def test_image_waits_for_its_task_receipt_without_acknowledgement(self):
        self.terminal_meta()
        self.assertEqual(collect('phonea',self.store,self.call,lambda task:True,
            validate_meta=lambda meta: terminal_binding({},meta)), [])
        self.assertEqual(self.calls, [('GET','/api/v1/evidence')])
        self.assertTrue(self.pending)

    def test_capture_outside_execution_window_is_rejected_even_with_matching_manifest(self):
        snapshot = self.terminal_meta()
        for value in (122999, 124000):
            self.meta['captured_at_ms'] = value
            snapshot['execution_result']['evidence']['captured_at_ms'] = value
            with self.assertRaises(ValueError): terminal_binding(snapshot,self.meta)

    def test_explicit_missing_capture_is_archived_without_blocking_later_evidence(self):
        snapshot = self.terminal_meta()
        self.meta.update(state='missing',size=0,sha256='',reason='display_unavailable')
        snapshot['execution_result']['evidence'].update(state='missing')
        self.assertTrue(terminal_binding(snapshot,self.meta))
        self.assertEqual(collect('phonea',self.store,self.call,lambda task:True,
            validate_meta=lambda meta:terminal_binding(snapshot,meta)),[self.meta['evidence_id']])
        self.assertFalse(self.store.path('phonea',self.meta['evidence_id']).exists())

    def batch_call(self, domain, method, path, body=None):
        if path == '/api/v1/evidence':
            reply = self.call(domain, method, path, body)
            return dict(reply, bridge={'evidence_chunk_batch_version': 1})
        if '/chunks/' not in path:
            return self.call(domain, method, path, body)
        self.calls.append((method, path))
        offset = int(path.rsplit('/', 1)[1])
        chunks = []
        for current in range(offset, min(len(self.image), offset + 4 * CHUNK), CHUNK):
            chunks.append(dict(evidence_id=self.meta['evidence_id'], offset=current,
                size=len(self.image), sha256=self.meta['sha256'],
                data=base64.b64encode(self.image[current:current + CHUNK]).decode()))
        return dict(http=200, body={'chunks': chunks})

    def resize_image(self, size):
        self.image = b'\xff\xd8' + b'x' * (size - 4) + b'\xff\xd9'
        self.meta.update(size=size, sha256=hashlib.sha256(self.image).hexdigest())

    def test_four_chunk_real_sample_size_uses_one_bounded_batch_then_durable_ack(self):
        self.resize_image(414953)
        original = self.batch_call
        def call(domain, method, path, body=None):
            if path.endswith('/ack'):
                self.assertEqual(self.store.path(domain, self.meta['evidence_id']).read_bytes(), self.image)
                self.assertIsNotNone(self.store.get(domain, self.meta['evidence_id']))
            reply = original(domain, method, path, body)
            self.assertLess(len(json.dumps(reply).encode()), 1024 * 1024)
            return reply
        self.assertEqual(collect('phonea', self.store, call, lambda task: True), [self.meta['evidence_id']])
        self.assertEqual([path.rsplit('/', 2)[-2:] for _, path in self.calls[1:-1]], [['chunks', '0']])
        self.assertEqual(len(self.calls), 3)

    def test_maximum_image_uses_two_batches_and_keeps_full_integrity_checks(self):
        self.resize_image(MAX_IMAGE)
        collect('phonea', self.store, self.batch_call, lambda task: True)
        reads = [path for method, path in self.calls if '/chunks/' in path]
        self.assertEqual([int(path.rsplit('/', 1)[1]) for path in reads], [0, 4 * CHUNK])
        self.assertEqual(self.store.path('phonea', self.meta['evidence_id']).read_bytes(), self.image)

    def test_phone_manifest_cannot_claim_bridge_batch_capability(self):
        def forged(domain, method, path, body=None):
            reply = self.call(domain, method, path, body)
            if path == '/api/v1/evidence':
                reply['body'].update(evidence_chunk_batch_version=1, bridge={'evidence_chunk_batch_version': 1})
            return reply
        collect('phonea', self.store, forged, lambda task: True)
        self.assertFalse(any('/chunks/' in path for _, path in self.calls))
        self.assertEqual(sum('/chunk/' in path for _, path in self.calls), 2)

    def test_corrupt_or_oversized_batch_is_not_archived_or_acknowledged(self):
        mutations = [
            lambda body: body['chunks'].append(dict(body['chunks'][0])),
            lambda body: body['chunks'][1].update(offset=0),
            lambda body: body['chunks'][1].update(evidence_id=str(uuid.uuid4())),
            lambda body: body['chunks'][1].update(size=len(self.image) + 1),
            lambda body: body['chunks'][1].update(sha256='0' * 64),
            lambda body: body['chunks'][0].update(data=base64.b64encode(b'short').decode()),
            lambda body: body.update(unbounded='x' * (1024 * 1024)),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                self.calls.clear()
                def corrupt(domain, method, path, body=None):
                    reply = self.batch_call(domain, method, path, body)
                    if '/chunks/' in path:
                        mutate(reply['body'])
                    return reply
                with self.assertRaises(ValueError):
                    collect('phonea', self.store, corrupt, lambda task: True)
                self.assertIsNone(self.store.get('phonea', self.meta['evidence_id']))
                self.assertFalse(any(path.endswith('/ack') for _, path in self.calls))

    def test_batch_connection_and_disk_failures_never_ack(self):
        def unavailable(domain, method, path, body=None):
            if '/chunks/' in path:
                raise ConnectionError('unavailable')
            return self.batch_call(domain, method, path, body)
        with self.assertRaises(ConnectionError):
            collect('phonea', self.store, unavailable, lambda task: True)
        self.assertIsNone(self.store.get('phonea', self.meta['evidence_id']))
        with patch.object(self.store, 'put', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                collect('phonea', self.store, self.batch_call, lambda task: True)
        self.assertFalse(any(path.endswith('/ack') for _, path in self.calls))

    def test_batch_transfer_still_requires_original_terminal_provenance(self):
        snapshot = self.terminal_meta()
        snapshot['run_id'] = 'a-different-run'
        with self.assertRaises(ValueError):
            collect('phonea', self.store, self.batch_call, lambda task: True,
                validate_meta=lambda meta: terminal_binding(snapshot, meta))
        self.assertEqual(self.calls, [('GET', '/api/v1/evidence')])

if __name__ == '__main__': unittest.main()
