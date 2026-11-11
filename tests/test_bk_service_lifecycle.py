import unittest
from unittest.mock import patch
import subprocess
import sys
import os
import json
from ops.bk_service_lifecycle import launch_environment, launch_official


class LauncherEnvironmentTests(unittest.TestCase):
    def test_runtime_flags_and_secrets_do_not_skip_fresh_bootstrap(self):
        parent={'__DNTNEEDRESPAWN__':'1','PYTHONHOME':'old','PYTHONPATH':'old',
                'PKGDIR':'old','RUNDIR':'old','PROPERTIES.REMOTE.CKEY':'secret',
                'ANDROID_ROOT':'/system','ANDROID_SOCKET_adbd':'42'}
        env=launch_environment(parent,'/runtime','/user')
        self.assertEqual(env['ANDROID_ROOT'],'/system')
        self.assertEqual(env['USRDIR'],'/user')
        self.assertNotIn('__DNTNEEDRESPAWN__',env)
        self.assertNotIn('PYTHONPATH',env)
        self.assertNotIn('PROPERTIES.REMOTE.CKEY',env)
        self.assertNotIn('ANDROID_SOCKET_adbd',env)

    @unittest.skipUnless(os.name == 'posix', 'Verify Unix session semantics in existing Linux runtime')
    def test_official_launcher_can_detach_from_detached_maintenance_helper(self):
        # Reproduce the real failure condition, without running FIRERPA/Android.
        # A detached helper must launch a non-session-leader child so that the
        # official launcher's own setsid remains legal.
        code = '''import os, subprocess, sys, json
from unittest.mock import patch
from ops.bk_service_lifecycle import launch_official
real = subprocess.Popen
def child(args, **kw):
    return real([sys.executable, '-c', 'import os; os.setsid(); print("detached")'], **kw)
with patch('ops.bk_service_lifecycle.subprocess.Popen', side_effect=child):
    p = launch_official({}, '/runtime', '/user', subprocess.PIPE)
    output, _ = p.communicate(timeout=5)
print(json.dumps({'helper_is_session_leader':os.getsid(0)==os.getpid(), 'code':p.returncode, 'output':output.decode().strip()}))
'''
        p = subprocess.run([sys.executable, '-c', code], capture_output=True,
                           timeout=10, start_new_session=True)
        self.assertEqual(p.returncode, 0, p.stderr.decode())
        result = json.loads(p.stdout)
        self.assertTrue(result['helper_is_session_leader'])
        self.assertEqual(result['code'], 0)
        self.assertEqual(result['output'], 'detached')
