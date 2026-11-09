"""Install the additive API extension in the existing single-phone pilot.

Upload bk_metrics.py, bk_telemetry.py and check_bk_telemetry.py to
data/user/phone-pilot/bk-server, and run_bk_server.py to data/user/phone-pilot.
This restarts ONLY service:server-0, not the shared supervisor service group.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path('/opt/phone-pilot-20261108/pigeon')
CONTAINER = 'phone-pilot-pigeon-pigeon-1'
ENTRY = '/usr/lib/python3/dist-packages/server/__main__.py'


def container(*args, **kwargs):
    return subprocess.run(['docker', 'exec', '-i', CONTAINER, *args], check=True, capture_output=True, **kwargs)


def write_entry(content):
    container('python3', '-c', 'import sys; from pathlib import Path; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())', ENTRY, input=content)


def wait_api():
    for _ in range(20):
        try:
            result = container('python3', '-c', 'import urllib.request,urllib.error;\ntry: r=urllib.request.urlopen("http://127.0.0.1:8800/api/v1/user/login",timeout=2); print(r.status)\nexcept urllib.error.HTTPError as e: print(e.code)')
            if result.stdout.strip() in (b'200', b'401'):
                return
        except subprocess.CalledProcessError:
            pass
        time.sleep(1)
    raise RuntimeError('API did not recover')


def main():
    # Validate imports, route registration and bounded sampling before touching startup.
    result = container('python3', '/user/phone-pilot/bk-server/check_bk_telemetry.py')
    print(result.stdout.decode().strip())
    original = container('cat', ENTRY).stdout
    config = ROOT / 'single-phone-supervisord.conf'
    before = config.read_text()
    old = 'python3 -u -m server --port=880%(process_num)s'
    new = 'python3 -u /user/phone-pilot/run_bk_server.py --port=880%(process_num)s'
    if old not in before:
        raise ValueError('Expected the original API startup command; inspect before retrying')
    if b'BK_TELEMETRY_EXTENSION' in original:
        raise ValueError('Extension is already installed; inspect rather than overwrite its recovery record')
    hook = b'''# BK_TELEMETRY_EXTENSION\nimport server\nserver.__path__.append('/user/phone-pilot/bk-server')\nfrom server.bk_telemetry import install\ninstall()\n'''
    marker = b'from .service import Service'
    if original.count(marker) != 1:
        raise ValueError('Unrecognized API entry point')
    patched = original.replace(marker, marker + b'\n' + hook)
    recovery = ROOT / 'data/user/phone-pilot/bk-telemetry-recovery'
    recovery.mkdir(exist_ok=False)
    (recovery / 'api-main.py').write_bytes(original)
    (recovery / 'supervisord.conf').write_text(before)
    (recovery / 'README.txt').write_text('BK device wall rollback, owner: current deployment task. Review: 2026-11-16.\nOnly API server restart is required. Keep until card/control acceptance.\n')
    try:
        # The live supervisor retains its old command. Hook that entry point now;
        # on container recreation the persistent config uses the additive launcher.
        write_entry(patched)
        config.write_text(before.replace(old, new, 1))
        print(container('supervisorctl', 'restart', 'service:server-0').stdout.decode().strip())
        wait_api()
    except BaseException:
        write_entry(original)
        config.write_text(before)
        container('supervisorctl', 'restart', 'service:server-0')
        raise
    print(json.dumps({'api_extension': 'installed', 'entry_sha256': hashlib.sha256(patched).hexdigest(), 'recovery': str(recovery)}))


if __name__ == '__main__':
    main()
