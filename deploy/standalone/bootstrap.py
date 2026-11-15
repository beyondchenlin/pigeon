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
    key = state / 'bk-gateway-secret'
    if not key.exists():
        import tempfile
        with tempfile.NamedTemporaryFile(dir=state) as staged:
            staged.write(secrets.token_hex(32).encode('ascii'))
            staged.flush()
            try:
                os.link(staged.name, key)
            except FileExistsError:
                pass
    from server.bk_native_gateway_api import load_secret
    load_secret()
    os.execv('/usr/local/bin/entry', ['entry'])


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
