"""Gateway attestation rejects unknown workers, configuration, and failures."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

if 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Run in the existing PIGEON runtime')

from server import bk_native_gateway_health as health


class GatewayHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bk-gateway-health-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.files = {name:root/name for name in ('config','access','log','health')}
        for name,path in self.files.items(): path.write_text('synthetic '+name)
        manifest = root/'manifest'
        manifest.write_text(json.dumps({'version':1,'sha256':{
            name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in self.files.items()}}))
        manifest.chmod(0o600)
        self.patches = [patch.object(health,'FILES',self.files), patch.object(health,'MANIFEST',manifest),
                        patch.object(health,'load_secret',return_value='a'*64)]
        for item in self.patches: item.start(); self.addCleanup(item.stop)

    def response(self, pid=42):
        result=io.BytesIO(json.dumps({'version':1,'worker_pid':pid}).encode())
        result.status=200
        return result

    def check(self, workers, pid=42):
        opener=Mock(); opener.open.return_value=self.response(pid)
        with patch.object(health,'nginx_workers',side_effect=workers), \
             patch.object(health.urllib.request,'build_opener',return_value=opener):
            return health.check_gateway()

    def test_one_verified_worker_is_required(self):
        self.assertTrue(self.check([{42:('7',False)}, {42:('7',False)}]))
        self.assertFalse(self.check([{42:('7',False),43:('8',True)}]))
        self.assertFalse(self.check([{42:('7',True)}]))
        self.assertFalse(self.check([{42:('7',False)}, {42:('8',False)}]))
        self.assertFalse(self.check([{42:('7',False)}], pid=99))

    def test_changed_config_fails_before_contact(self):
        self.files['access'].write_text('changed')
        with patch.object(health,'nginx_workers') as workers:
            self.assertFalse(health.check_gateway())
            workers.assert_not_called()

    def test_unreadable_worker_state_fails_closed(self):
        with patch.object(health,'nginx_workers',side_effect=RuntimeError('unavailable')):
            self.assertFalse(health.check_gateway())
