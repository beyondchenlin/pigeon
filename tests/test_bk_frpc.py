"""Forwarding adapter contract and safe replacement regression checks."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ops.bk_frpc_adapter import official_args, main, recovery_config
from ops.install_bk_frpc import atomic, sha, activate
from ops.install_bk_frps import linux_shell


class ForwardingAdapterTests(unittest.TestCase):
    def test_activation_waits_for_a_new_pid_when_restarting_same_version(self):
        from types import SimpleNamespace
        with patch('ops.install_bk_frpc.Path') as path, \
                patch('ops.install_bk_frpc.os.kill') as kill, \
                patch('ops.install_bk_frpc.time.sleep') as sleep, \
                patch('ops.install_bk_frpc.process_ids', side_effect=[[11],[22]]):
            path.return_value.stat.return_value = SimpleNamespace(st_uid=2000)
            path.return_value.joinpath.return_value.stat.return_value = SimpleNamespace(st_dev=2,st_ino=3)
            activate((11,2,3), '/verified-client')
            kill.assert_called_once()
            sleep.assert_called_once_with(1)

    def test_windows_checkout_shell_is_executable_on_linux(self):
        self.assertEqual(linux_shell(b'#!/bin/sh\r\nexec "$@"\r\n'), b'#!/bin/sh\nexec "$@"\n')
        with self.assertRaises(ValueError):linux_shell(b'not a shell script')

    def test_native_cli_preserves_config_path(self):
        config = '/data/local/tmp/one phone/.local/run/fwd.toml'
        self.assertEqual(official_args(['-f', '-c', config, '-s']), ['-c', config])

    def test_versions_and_help_do_not_start_a_second_daemon(self):
        self.assertEqual(official_args(['-v']), ['--version'])
        self.assertEqual(official_args(['-h']), ['--help'])

    def test_missing_and_unknown_args_fail_closed_without_leaking_values(self):
        for args in ([], ['-c'], ['--token', 'sensitive-value'], ['-c', 'a', 'extra']):
            with self.subTest(args=args), self.assertRaises(ValueError) as error:
                official_args(args)
            self.assertNotIn('sensitive-value', str(error.exception))

    def test_exec_replaces_supervised_child_and_preserves_native_config(self):
        with patch.dict(os.environ, {'USRDIR':'/data/local/tmp/usr'}), \
                patch('sys.argv', ['xfrpc', '-f', '-c', '/device-specific.toml']), \
                patch('ops.bk_frpc_adapter.write_recovery_config', return_value='/derived.json') as derive, \
                patch('os.execv') as execute:
            main()
        binary=str(Path('/data/local/tmp/usr')/'phone-pilot/frpc-0.72.0-bk.1-android-arm64')
        derive.assert_called_once()
        execute.assert_called_once_with(binary, [binary, '-c', '/derived.json'])

    def test_recovery_overrides_preserve_native_auth_and_device_routing(self):
        with tempfile.TemporaryDirectory(prefix='bk-recovery-test-') as directory:
            p=Path(directory)/'fwd.toml'
            original='serverAddr="host"\nserverPort=7000\n[auth]\ntoken="test-secret"\n[metadatas]\ndevice="one"\n[transport.tls]\nenable=true\n[[proxies]]\nname="lamda"\ntype="tcp"\nlocalPort=1234\n'
            p.write_text(original)
            c=recovery_config(p)
            self.assertEqual(c['auth'], {'token':'test-secret'})
            self.assertEqual(c['metadatas'], {'device':'one'})
            self.assertEqual(c['proxies'][0]['localPort'],1234)
            self.assertTrue(c['transport']['tls']['enable'])
            self.assertEqual(c['transport']['heartbeatTimeout'],10)
            self.assertTrue(c['transport']['tcpMux'])
            self.assertFalse(c['loginFailExit'])
            self.assertEqual(p.read_text(), original)

    def test_atomic_replacement_and_recovery_content(self):
        with tempfile.TemporaryDirectory(prefix='bk-frpc-unit-') as directory:
            entry=Path(directory)/'xfrpc'
            entry.write_bytes(b'original')
            original=entry.read_bytes()
            atomic(entry,b'adapter')
            self.assertEqual(entry.read_bytes(),b'adapter')
            atomic(entry,original)
            self.assertEqual(entry.read_bytes(),b'original')
            self.assertEqual(len(sha(entry)),64)
            self.assertFalse(entry.with_name('xfrpc.bk-install').exists())

    def test_unexpected_staging_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='bk-frpc-unit-') as directory:
            entry=Path(directory)/'xfrpc'
            entry.write_bytes(b'original')
            pending=entry.with_name('xfrpc.bk-install')
            pending.write_bytes(b'another operation')
            with self.assertRaises(FileExistsError):atomic(entry,b'adapter')
            self.assertEqual(entry.read_bytes(),b'original')
            self.assertEqual(pending.read_bytes(),b'another operation')


if __name__ == '__main__':
    unittest.main()
