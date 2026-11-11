import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ops'))
import bk_maintenance_mode as mode
import bk_maintenance as phone
import bk_maintenance_host as host


class ModeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bk-maintenance-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_missing_or_corrupt_config_is_off(self):
        self.assertFalse(mode.enabled(self.root))
        path=self.root/'phone-pilot/lifecycle/maintenance.json'
        mode.write_json(path,{'enabled':'true','request_id':str(uuid.uuid4())})
        self.assertFalse(mode.enabled(self.root))
        mode.write_json(path,{'enabled':True,'request_id':str(uuid.uuid4())})
        self.assertTrue(mode.enabled(self.root))

    def test_termux_environment_preserves_android_boot_paths_not_service_secrets(self):
        env=phone.termux_environment({'BOOTCLASSPATH':'boot','DEX2OATBOOTCLASSPATH':'dex',
                                     '__DNTNEEDRESPAWN__':'1','TOKEN':'secret','LD_LIBRARY_PATH':'native'})
        self.assertEqual(env['BOOTCLASSPATH'],'boot')
        self.assertEqual(env['DEX2OATBOOTCLASSPATH'],'dex')
        self.assertTrue({'TOKEN','__DNTNEEDRESPAWN__','LD_LIBRARY_PATH'}.isdisjoint(env))
        with self.assertRaises(ValueError):phone.termux_environment({})

    @unittest.skipUnless(os.name=='posix','File lock requires POSIX runtime')
    def test_duplicate_phone_switch_does_not_start_worker_again(self):
        request_id=str(uuid.uuid4())
        root=self.root/'phone-pilot/lifecycle'
        mode.write_json(root/'maintenance.json',dict(enabled=False,request_id=request_id))
        with patch.object(phone,'USER',self.root),patch.object(phone,'ROOT',root), \
             patch.object(phone,'status',return_value={'enabled':False}),patch.object(phone,'spawn_pipes') as spawn:
            phone.set_mode(dict(action='set',enabled=False,request_id=request_id))
            spawn.assert_not_called()
            with self.assertRaises(ValueError):phone.set_mode(dict(action='set',enabled=True,request_id=request_id))

    def test_host_off_stops_only_maintenance_and_disconnects_only_reserved_transport(self):
        request_id=str(uuid.uuid4());calls=[];adb_calls=[]
        mode.write_json(self.root/'host-request.json',dict(enabled=False,request_id=request_id))
        mode.write_json(self.root/'host.enabled',{})
        transport=['127.0.0.1:15555\tdevice\nother\tdevice\n']
        def systemctl(*args):
            calls.append(args);return subprocess.CompletedProcess(args,0,'inactive\n','')
        def adb(command):
            adb_calls.append(command)
            if command.startswith('host:disconnect:'):
                transport[0]='other\tdevice\n';return ''
            return transport[0]
        result=host.apply(self.root,systemctl,adb)
        self.assertFalse(result['active']);self.assertTrue(result['transport_disconnected'])
        self.assertEqual(calls,[('stop',host.SERVICE),('is-active',host.SERVICE)])
        self.assertIn('host:disconnect:127.0.0.1:15555',adb_calls)
        self.assertFalse((self.root/'host.enabled').exists())

    def test_invalid_host_mode_fails_closed(self):
        mode.write_json(self.root/'host-request.json',{'enabled':'yes','request_id':'invalid'})
        calls=[]
        def systemctl(*args):
            calls.append(args);return subprocess.CompletedProcess(args,0,'inactive\n','')
        result=host.apply(self.root,systemctl,lambda command:'')
        self.assertFalse(result['enabled']);self.assertEqual(calls[0],('stop',host.SERVICE))


if __name__=='__main__':unittest.main()
