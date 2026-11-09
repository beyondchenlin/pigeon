"""Configure authenticated coturn on the existing pilot host (coturn package required).

Does not change nginx or start the stopped STF application. Cloud firewall must
also allow TCP/UDP 3478 and UDP 49160:49200. Shared secrets never leave the host.
"""
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess

ROOT = Path('/opt/phone-pilot-20261108/pigeon/data/user/phone-pilot')
CONFIG = ROOT / 'bk-turn.json'
assert os.geteuid() == 0 and ROOT.is_dir()
turn = pwd.getpwnam('turnserver')
if CONFIG.exists():
    config = json.loads(CONFIG.read_text())
else:
    config = {'secret': secrets.token_hex(32),
              'stun_urls': ['stun:dongfengqi.top:3478'],
              'turn_urls': ['turn:dongfengqi.top:3478?transport=udp', 'turn:dongfengqi.top:3478?transport=tcp']}
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(config, f)
directory = Path('/etc/bk-turn')
directory.mkdir(mode=0o750, exist_ok=True)
os.chown(directory, 0, turn.pw_gid)
conf = directory / 'turnserver.conf'
body = f'''listening-port=3478
listening-ip=172.17.23.90
relay-ip=172.17.23.90
external-ip=47.242.93.105/172.17.23.90
min-port=49160
max-port=49200
realm=dongfengqi.top
fingerprint
use-auth-secret
static-auth-secret={config['secret']}
no-tls
no-dtls
no-cli
no-multicast-peers
no-tcp-relay
stale-nonce=600
max-allocate-lifetime=600
user-quota=4
total-quota=32
max-bps=1000000
bps-capacity=16000000
relay-threads=1
pidfile=/run/bk-turn/turnserver.pid
log-file=/var/log/bk-turn/turn.log
simple-log
no-stdout-log
denied-peer-ip=0.0.0.0-0.255.255.255
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=100.64.0.0-100.127.255.255
denied-peer-ip=127.0.0.0-127.255.255.255
denied-peer-ip=169.254.0.0-169.254.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
denied-peer-ip=224.0.0.0-255.255.255.255
denied-peer-ip=::1
denied-peer-ip=fc00::-fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff
denied-peer-ip=fe80::-febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff
'''
conf.write_text(body)
conf.chmod(0o640)
os.chown(conf, 0, turn.pw_gid)
Path('/etc/systemd/system/bk-turn.service').write_text('''[Unit]
Description=BK authenticated WebRTC relay
After=network-online.target
Wants=network-online.target
[Service]
User=turnserver
Group=turnserver
ExecStart=/usr/bin/turnserver -c /etc/bk-turn/turnserver.conf
Restart=on-failure
RestartSec=3
RuntimeDirectory=bk-turn
LogsDirectory=bk-turn
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/run/bk-turn /var/log/bk-turn
MemoryMax=128M
LimitNOFILE=4096
[Install]
WantedBy=multi-user.target
''')
Path('/etc/logrotate.d/bk-turn').write_text('''/var/log/bk-turn/turn.log {
    daily
    maxsize 20M
    rotate 5
    compress
    missingok
    notifempty
    copytruncate
    su turnserver turnserver
}
''')
for port in ('3478/tcp', '3478/udp', '49160:49200/udp'):
    subprocess.run(['ufw', 'allow', port, 'comment', 'BK WebRTC authenticated relay'], check=True)
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'enable', '--now', 'bk-turn'], check=True)
subprocess.run(['systemctl', 'is-active', 'bk-turn'], check=True)
print('BK TURN configured; credentials retained only in root-owned server files')
