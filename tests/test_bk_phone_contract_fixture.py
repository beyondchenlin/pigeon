"""Consume a synthetic fixture emitted by the phone's production serializers.

Regenerate with BK101 BrowseExecutionTest.writeCrossEndFixtureFromProductionSerializers.
This verifies the wire contract and archive bytes, never real-device evidence.
"""
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from server.bk_execution import validate_execution, supervision_timeout
from server.bk_evidence_store import Archive, CHUNK, collect, terminal_binding


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'bk-browse-contract-v1.json'


class PhoneContractFixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.assertIs(self.fixture['synthetic'], True)

    def test_phone_execution_is_accepted_without_field_translation(self):
        execution = self.fixture['execution']
        self.assertEqual(validate_execution(execution), execution)
        self.assertEqual(self.fixture['snapshot']['execution'], execution)
        self.assertEqual(supervision_timeout(execution), 120 + execution['duration_s']
            + execution['finish_timeout_s'] + execution['stop_timeout_s'] + execution['event_latency_s'])

    def test_phone_terminal_satisfies_bridge_release_and_is_bound_to_actual_run(self):
        spec = importlib.util.spec_from_file_location('phone_bridge_fixture', ROOT / 'ops' / 'bk_ai_phone.py')
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        snapshot = self.fixture['snapshot']
        self.assertTrue(bridge.terminal_receipt({'http':200, 'body':snapshot},
            snapshot['task_id'], self.fixture['execution']))
        self.assertTrue(terminal_binding(snapshot, self.fixture['evidence']))
        self.assertEqual(snapshot['run_id'], self.fixture['evidence']['run_id'])

    def test_phone_generated_jpeg_round_trips_through_checked_archive_and_ack(self):
        snapshot, meta = self.fixture['snapshot'], self.fixture['evidence']
        data = base64.b64decode(self.fixture['image_base64'], validate=True)
        acknowledgements = []
        def call(domain, method, path, body=None):
            self.assertEqual(domain, 'syntheticphone')
            if path == '/api/v1/evidence':
                return {'http':200, 'body':{'evidence':[meta]}}
            if '/chunk/' in path:
                offset = int(path.rsplit('/',1)[1])
                return {'http':200, 'body':dict(evidence_id=meta['evidence_id'], offset=offset,
                    size=meta['size'], sha256=meta['sha256'],
                    data=base64.b64encode(data[offset:offset+CHUNK]).decode())}
            self.assertEqual(path, '/api/v1/evidence/' + meta['evidence_id'] + '/ack')
            acknowledgements.append(body)
            return {'http':200, 'body':{'archived':True}}
        with tempfile.TemporaryDirectory() as root:
            archive = Archive(root)
            ids = collect('syntheticphone', archive, call,
                lambda task_id: task_id == snapshot['task_id'],
                validate_meta=lambda value: terminal_binding(snapshot,value))
            self.assertEqual(ids,[meta['evidence_id']])
            self.assertEqual(archive.path('syntheticphone',meta['evidence_id']).read_bytes(),data)
            self.assertEqual(archive.get('syntheticphone',meta['evidence_id'])['device_domain'],'syntheticphone')
            self.assertEqual(acknowledgements,[{'sha256':meta['sha256']}])


if __name__ == '__main__':
    unittest.main()