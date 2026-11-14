"""Conservative check that no untracked old nginx worker can serve traffic."""
import hashlib
import json
from pathlib import Path
import re
import stat
import urllib.request

from .bk_native_gateway_api import load_secret

ROOT = Path('/user/phone-pilot')
MANIFEST = ROOT / 'bk-gateway-health.json'
FILES = {
    'config': Path('/usr/local/openresty/nginx/conf/nginx.conf'),
    'access': ROOT / 'bk_native_gateway_access.lua',
    'log': ROOT / 'bk_native_gateway_log.lua',
    'health': ROOT / 'bk_native_gateway_health.lua',
}
URL = 'http://127.0.0.1:18981/_bk_gateway_health'


def nginx_workers(proc=Path('/proc')):
    """Return identities, including draining workers; unknown reads fail closed."""
    workers = {}
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            raw = (entry / 'cmdline').read_bytes().replace(b'\0', b' ')
            if not raw.startswith(b'nginx: worker process'):
                continue
            stat_data = (entry / 'stat').read_text()
            start = stat_data.rsplit(')', 1)[1].split()[19]
            workers[int(entry.name)] = (start, b'shutting down' in raw)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, IndexError, UnicodeError):
            raise RuntimeError('worker_identity_unavailable') from None
    return workers


def check_gateway():
    """Verify immutable inputs and the single active, non-draining worker."""
    try:
        info = MANIFEST.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            return False
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        expected = manifest.get('sha256')
        if manifest.get('version') != 1 or not isinstance(expected, dict) or set(expected) != set(FILES):
            return False
        for name, path in FILES.items():
            digest = expected[name]
            if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return False
        before = nginx_workers()
        if len(before) != 1 or next(iter(before.values()))[1]:
            return False
        request = urllib.request.Request(URL, headers={'X-BK-Gateway-Key': load_secret()})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=2) as response:
            if response.status != 200:
                return False
            value = json.loads(response.read(1024))
        worker = value.get('worker_pid')
        return (value.get('version') == 1 and type(worker) is int and worker in before
                and before == nginx_workers())
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError, UnicodeError):
        return False
