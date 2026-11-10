"""Stage the BK server-only WireGuard endpoint; enroll no production clients.

Debian 12 with wireguard-tools and UFW already installed. This does not change
PIGEON, Docker, the default route, forwarding policy or the phone runtime.
"""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess

INTERFACE = 'wg-bk'
ADDRESS = '10.77.0.1/24'
PORT = 51820
PUBLIC_ENDPOINT = '47.242.93.105:51820'
STATE = Path('/opt/phone-pilot-20261108/ops/wireguard-20261110')
CONFIG = Path('/etc/wireguard/wg-bk.conf')


def run(*args, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True, check=True)


def create(path, content, mode=0o600):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content); stream.flush(); os.fsync(stream.fileno())


def main():
    if os.getuid() != 0: raise RuntimeError('Server root access required')
    for directory in (STATE, CONFIG.parent):
        if directory.is_symlink(): raise RuntimeError('Unexpected directory symlink')
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    manifest = STATE/'installation.json'
    owned = json.loads(manifest.read_text()) if manifest.exists() else None
    if CONFIG.is_symlink() or (CONFIG.exists() and not owned):
        raise RuntimeError('Existing config is not owned by this deployment')
    if owned:
        if hashlib.sha256(CONFIG.read_bytes()).hexdigest() != owned['config_sha256']:
            raise RuntimeError('Config has changed; inspect before modifying it')
    else:
        routes = json.loads(run('ip', '-j', 'route', 'show', 'table', 'all').stdout)
        target = ipaddress.ip_network(ADDRESS, strict=False)
        for route in routes:
            dst = route.get('dst', 'default')
            if dst == 'default': continue
            network = ipaddress.ip_network(dst, strict=False)
            if network.version == 4 and target.overlaps(network):
                raise RuntimeError('Reserved tunnel network overlaps an existing route')
        for name in ('user.rules', 'user6.rules'):
            source = Path('/etc/ufw')/name
            saved = STATE/(name+'.before')
            if source.exists() and not saved.exists():
                create(saved, source.read_text())
        private = run('wg', 'genkey').stdout.strip()
        config = f'[Interface]\nAddress = {ADDRESS}\nListenPort = {PORT}\nPrivateKey = {private}\nMTU = 1420\n'
        create(CONFIG, config)
        del private, config
        owned = dict(interface=INTERFACE, address=ADDRESS, endpoint=PUBLIC_ENDPOINT,
            config_sha256=hashlib.sha256(CONFIG.read_bytes()).hexdigest(), peers_enrolled=0,
            owner='BK phone pilot', stage='server-only', review_after='2026-11-17')
        create(manifest, json.dumps(owned, indent=2)+'\n')
    # Add only IPv4 UDP on the existing external interface. Keep all current
    # rules and default deny policies; no broad tunnel/LAN forwarding yet.
    run('ufw', 'allow', 'in', 'on', 'eth0', 'proto', 'udp', 'from', '0.0.0.0/0',
        'to', 'any', 'port', str(PORT), 'comment', 'BK WireGuard server')
    run('systemctl', 'enable', '--now', 'wg-quick@'+INTERFACE)
    if run('wg', 'show', INTERFACE, 'listen-port').stdout.strip() != str(PORT):
        raise RuntimeError('Unexpected listening port')
    if run('wg', 'show', INTERFACE, 'peers').stdout.strip():
        raise RuntimeError('Unexpected peers in server-only stage')
    result = {**owned, 'active': run('systemctl', 'is-active', 'wg-quick@'+INTERFACE).stdout.strip(),
        'enabled': run('systemctl', 'is-enabled', 'wg-quick@'+INTERFACE).stdout.strip(),
        'public_key': run('wg', 'show', INTERFACE, 'public-key').stdout.strip()}
    public_path = STATE/'server-public.json'
    if not public_path.exists(): create(public_path, json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__': main()
