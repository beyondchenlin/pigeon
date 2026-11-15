import base64
import hashlib
import tempfile
import unittest
import uuid
from unittest.mock import patch
from server.bk_ai_transport import NATIVE_INFO
from server.bk_evidence_store import Archive
from server.bk_process import sync_device, timeline


class ProcessBackfillTests(unittest.TestCase):
    def test_reconnect_after_partial_image_keeps_progress_and_stop(self):
        domain, task, run = 'pilot', str(uuid.uuid4()), '20261115-211603-erp3b5'
        data = b'\xff\xd8' + bytes(105000) + b'\xff\xd9'
        events = [{'type':'tool_result', 'step':4, 'atMs':80, 'tool':'scroll'},
                  {'type':'media', 'step':4, 'atMs':90, 'mediaId':8, 'terminal':True},
                  {'type':'media', 'step':3, 'atMs':75, 'mediaId':7},
                  {'type':'task_ended', 'atMs':100, 'outcome':'cancelled'},
                  {'type':'model_calls', 'atMs':101, 'calls':4, 'inputTokens':200, 'outputTokens':60}]
        fail = True
        def phone(d, method, path, **kwargs):
            self.assertEqual((d,method), (domain,'GET'))
            self.assertEqual(kwargs['task_info'], NATIVE_INFO)
            offset = int(path.rsplit('/',1)[1])
            if '/process/' in path:
                body = dict(offset=offset,total=len(events),events=events[offset:],run={'id':run,'endedAtMs':100})
            else:
                media = int(path.split('/')[-2])
                if fail and media == 8 and offset:
                    raise ConnectionError()
                piece = data[offset:offset+96*1024]
                body = dict(available=True, media_id=media, offset=offset,size=len(data),
                            sha256=hashlib.sha256(data).hexdigest(),data=base64.b64encode(piece).decode())
            return {'http':200, 'body':dict(body,task_id=task,run_id=run)}
        tasks = [(task,run,True,NATIVE_INFO)]
        with tempfile.TemporaryDirectory(prefix='bk-process-') as root, patch('server.bk_process.time.time', return_value=1000) as clock:
            store = Archive(root)
            with self.assertRaises(ConnectionError):
                sync_device(store,phone,domain,tasks)
            self.assertEqual(timeline(store,domain,task,0)['total'],5)
            self.assertEqual(len(store.list(domain,{task})),1)
            self.assertIsNotNone(timeline(store,domain,task,0)['events'][2]['image'])
            clock.return_value += 61
            fail = False
            for _ in range(2):
                sync_device(Archive(root),phone,domain,tasks)
            process = timeline(store,domain,task,0)
            self.assertEqual([r['seq'] for r in process['events']],list(range(5)))
            self.assertEqual(store.path(domain,process['events'][1]['image']).read_bytes(),data)
            self.assertEqual(len(store.list(domain,{task})),2)
            self.assertIsNone(timeline(store,'unrelated',task,0))
