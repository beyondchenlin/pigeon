"""Initialize a fresh BK installation and start its supervised services."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys


def make_manifest():
    lua = Path(os.environ['BK_GATEWAY_LUA_DIR'])
    files = {'config': Path('/usr/local/openresty/nginx/conf/nginx.conf')}
    for name in ('access', 'log', 'health'):
        files[name] = lua / f'bk_native_gateway_{name}.lua'
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    target = Path(os.environ['BK_GATEWAY_MANIFEST'])
    target.write_text(json.dumps({'version': 1, 'sha256': hashes}))
    target.chmod(0o600)


def start():
    if len(os.environ.get('PIGEON_INITIAL_PASSWORD', '')) < 16:
        sys.exit('Set PIGEON_INITIAL_PASSWORD to a generated initial password (16+ characters).')
    state = Path('/user/phone-pilot')
    state.mkdir(mode=0o700, exist_ok=True)
    # Seed the per-device policy once. An upgrade preserves the operator's
    # settings, including installations with more than one API process.
    devices = state / 'bk-ai-devices.json'
    if not devices.exists():
        seed_file(devices, Path(__file__).with_name('devices.json').read_bytes())
    key = state / 'bk-gateway-secret'
    if not key.exists():
        seed_file(key, secrets.token_hex(32).encode('ascii'))
    from server.bk_native_gateway_api import load_secret
    load_secret()
    os.execv('/usr/local/bin/entry', ['entry'])


def seed_file(target, contents):
    import tempfile
    with tempfile.NamedTemporaryFile(dir=target.parent) as staged:
        staged.write(contents)
        staged.flush()
        os.fsync(staged.fileno())
        try:
            os.link(staged.name, target)
        except FileExistsError:
            pass


if __name__ == '__main__':
    if sys.argv[1:] == ['--make-manifest']:
        make_manifest()
    elif sys.argv[1:] == ['--frps']:
        from wait_auth_then_frps import wait_for_auth
        wait_for_auth()
        os.execv('/usr/local/bin/frps.sh', ['frps.sh', '-c', '/etc/frps.toml'])
    elif not sys.argv[1:]:
        start()
    else:
        sys.exit('Unknown bootstrap option.')
