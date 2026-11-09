"""Expose the BK console at the existing authenticated /d/<id>/ URL."""
from pathlib import Path
import subprocess

ROOT = Path('/opt/phone-pilot-20261108/pigeon')
CONTAINER = 'phone-pilot-pigeon-pigeon-1'
NGINX = '/usr/local/openresty/nginx/sbin/nginx'


def nginx(*args):
    subprocess.run(['docker', 'exec', CONTAINER, NGINX, *args], check=True)


def main():
    config = ROOT / 'single-phone-nginx.conf'
    original = config.read_text()
    marker = 'rewrite_by_lua_file ngx_op_api_gateway_validate.luac;'
    hook = 'access_by_lua_file /user/phone-pilot/bk_device_console.lua;'
    if hook in original:
        nginx('-t')
        return
    if original.count(marker) != 1:
        raise ValueError('Expected exactly one authenticated device gateway')
    changed = original.replace(marker, marker + '\n        ' + hook, 1)
    backup = ROOT / 'data/user/phone-pilot/bk-telemetry-recovery/nginx-before-console.conf'
    with backup.open('x') as stream:
        stream.write(original)
    try:
        config.write_text(changed)
        nginx('-t')
        nginx('-s', 'reload')
    except BaseException:
        config.write_text(original)
        nginx('-t')
        nginx('-s', 'reload')
        raise
    print('BK console installed at existing /d/<id>/; gateway authentication and phone sockets preserved')


if __name__ == '__main__':
    main()
