import base64
import hashlib
import tempfile
import unittest
import uuid
from server.bk_evidence_store import Archive, collect, CHUNK


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

if __name__ == '__main__': unittest.main()
