"""Bounded kernel handshake test; no physical router/Internet claim.

Creates an ephemeral network namespace and peer, then always removes both.
The UDP socket is born in the host namespace, so loopback is its underlay.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

def run(*args, input=None, check=True):
    return subprocess.run(args, input=input, text=True, capture_output=True, check=check, timeout=12)

def main():
    assert os.getuid()==0
    server='wg-bk'; name='bk-wg-check'; link='bk-wg-probe'
    if run('wg','show',server,'peers').stdout.strip():
        raise RuntimeError('Server-only probe refuses an already enrolled production peer')
    if run('ip','netns','list').stdout.find(name)>=0 or run('ip','link','show',link,check=False).returncode==0:
        raise RuntimeError('Previous probe resources exist; inspect instead of overwriting')
    state=Path('/opt/phone-pilot-20261108/ops/wireguard-20261110')
    server_key=run('wg','show',server,'public-key').stdout.strip()
    peer=None; made_ns=False; made_link=False
    try:
        with tempfile.TemporaryDirectory(prefix='handshake-',dir=state) as tmp:
            key=run('wg','genkey').stdout
            peer=run('wg','pubkey',input=key).stdout.strip()
            keyfile=Path(tmp)/'client.key'; keyfile.write_text(key); keyfile.chmod(0o600); del key
            run('ip','netns','add',name); made_ns=True
            run('ip','link','add',link,'type','wireguard'); made_link=True
            run('wg','set',link,'private-key',str(keyfile),'peer',server_key,
                'allowed-ips','10.77.0.1/32','endpoint','127.0.0.1:51820','persistent-keepalive','1')
            run('wg','set',server,'peer',peer,'allowed-ips','10.77.0.254/32')
            run('ip','link','set',link,'netns',name); made_link=False
            run('ip','-n',name,'address','add','10.77.0.254/24','dev',link)
            run('ip','-n',name,'link','set','lo','up')
            run('ip','-n',name,'link','set',link,'up')
            # UFW already permits echo requests; no firewall modification.
            ping=run('ip','netns','exec',name,'ping','-c','3','-W','2','10.77.0.1')
            handshakes=run('wg','show',server,'latest-handshakes').stdout.splitlines()
            stamp=next(int(row.split()[1]) for row in handshakes if row.split()[0]==peer)
            assert 0<=time.time()-stamp<30
            transfer=run('wg','show',server,'transfer').stdout.splitlines()
            received,sent=next([int(x) for x in row.split()[1:]] for row in transfer if row.split()[0]==peer)
            assert received>0 and sent>0
            print(json.dumps({'encrypted_handshake':True,'echo_roundtrips':3,'received_bytes':received,
                'sent_bytes':sent,'scope':'same-host isolated namespace, not home-router acceptance'}))
    finally:
        if peer:run('wg','set',server,'peer',peer,'remove',check=False)
        if made_ns:run('ip','netns','delete',name,check=False)
        if made_link:run('ip','link','delete',link,check=False)
    assert not run('wg','show',server,'peers').stdout.strip()
    assert name not in run('ip','netns','list').stdout
    print(json.dumps({'probe_removed':True,'production_peers':0}))

if __name__=='__main__':main()
