import unittest
from ops.bk_service_lifecycle import launch_environment


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
