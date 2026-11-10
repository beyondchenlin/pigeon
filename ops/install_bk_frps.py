"""Upgrade only FRP in the existing pilot, preserving auth ordering and rollback.

Run on the server after staging the verified official binary and this repo's
frps.sh under STAGE. No image rebuild or other service restart is necessary.
"""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess

ROOT = Path('/opt/phone-pilot-20261108/pigeon')
STAGE = ROOT.parent / 'ops/frp-stability-20261110'
TASK = ROOT / 'data/user/phone-pilot'
CONTAINER = 'phone-pilot-pigeon-pigeon-1'
EXPECTED = '4ad311e0630cf12945041143b926f515458836e896ff93c253d43c98cf304b89'


def docker(*args):
    result = subprocess.run(['docker', 'exec', CONTAINER, *args],
                            capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('FRP deployment command failed: ' + args[0])
    return result.stdout.decode().strip()


def replace(path, data, mode):
    temp = path.with_name(path.name + '.bk-install')
    with temp.open('xb') as stream:
        stream.write(data)
    temp.chmod(mode)
    os.replace(temp, path)


def linux_shell(data):
    result = data.replace(b'\r\n', b'\n')
    if not result.startswith(b'#!/bin/sh\n'):
        raise ValueError('Unexpected forwarding launcher shebang')
    return result


def main():
    if shutil.disk_usage(TASK).free < 10 * 1024**3 + 100 * 1024**2:
        raise RuntimeError('Not enough free disk space')
    binary = STAGE / 'frps-0.72.0-linux-amd64'
    if hashlib.sha256(binary.read_bytes()).hexdigest() != EXPECTED:
        raise RuntimeError('Official binary SHA-256 mismatch')
    launcher = TASK / 'wait_auth_then_frps.py'
    original = launcher.read_bytes()
    old = b"os.execv('/usr/local/bin/frps.sh', ['/usr/local/bin/frps.sh', '-c', '/etc/frps.toml'])"
    new = b"os.environ['BK_FRPS_BIN'] = '/user/phone-pilot/frps-0.72.0-linux-amd64'\n        os.execv('/user/phone-pilot/bk-frps.sh', ['/user/phone-pilot/bk-frps.sh', '-c', '/etc/frps.toml'])"
    if original.count(old) != 1:
        raise RuntimeError('Unrecognized launcher; inspect rather than overwrite')
    recovery = STAGE / 'wait_auth_then_frps.before.py'
    if recovery.exists():
        if recovery.read_bytes() != original:
            raise RuntimeError('Recovery record differs from the active launcher')
    else:
        recovery.write_bytes(original)
        recovery.chmod(0o600)
    replace(TASK / binary.name, binary.read_bytes(), 0o755)
    # A Windows checkout may use CRLF; Linux exec must see a clean shebang.
    shell = linux_shell((STAGE / 'frps.sh').read_bytes())
    replace(TASK / 'bk-frps.sh', shell, 0o755)
    docker('sh', '-n', '/user/phone-pilot/bk-frps.sh')
    print(docker('/user/phone-pilot/' + binary.name, 'verify', '-c', '/etc/frps.toml'))
    replace(launcher, original.replace(old, new), 0o755)
    try:
        print(docker('supervisorctl', 'restart', 'service:frps'))
        print(docker('/user/phone-pilot/' + binary.name, '--version'))
        status = docker('supervisorctl', 'status', 'service:frps')
        if 'RUNNING' not in status:
            raise RuntimeError('FRP did not become healthy')
        print(status)
    except BaseException:
        replace(launcher, original, 0o755)
        docker('supervisorctl', 'restart', 'service:frps')
        raise


if __name__ == '__main__':
    main()
