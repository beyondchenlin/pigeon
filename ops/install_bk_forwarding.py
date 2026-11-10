"""Install the recovery client through FIRERPA's service-command configuration.

Run inside the authorized shell runtime with its module search path. No native
binary is modified. Activation is separate: bk_forward_runtime.configure in a
detached helper, so the caller can survive its own forwarding connection closing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex

from install_bk_rtc import atomic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if os.getuid() != 2000: raise RuntimeError('Requires authorized shell UID 2000')
    root, user = Path(os.environ['ROOTDIR']).resolve(), Path(os.environ['USRDIR']).resolve()
    if not all(p.is_relative_to('/data/local/tmp') for p in (root, user)):
        raise RuntimeError('Unexpected runtime boundary')
    task = user/'phone-pilot/bk-forwarding'
    if task.is_symlink(): raise RuntimeError('Unexpected forwarding directory link')
    task.mkdir(mode=0o700, exist_ok=True)
    manifest_path = task/'install.json'
    properties = user/'.local/properties'
    if properties.is_symlink() or manifest_path.is_symlink(): raise RuntimeError('Unexpected configuration link')
    import lamda.services as services
    service = services.manager.get('fwd')
    current = service.get_config('command')
    command = shlex.join([str(root/'bin/python3.12'), '-u', str(task/'bk_fwd_service.py')])
    if args.rollback:
        manifest = json.loads(manifest_path.read_text())
        if current not in (manifest['command'],manifest['previous_command']):
            raise RuntimeError('Forwarding configuration changed; inspect before rollback')
        manifest['installed']=False;atomic(manifest_path,json.dumps(manifest,indent=2).encode())
        from bk_forward_runtime import configure
        from lamda.globals import supervisor
        configure(supervisor,str(user),restore=True)
        print(json.dumps({'installed':False,'activation_required':True}));return
    if manifest_path.exists():
        raise RuntimeError('Installation exists; inspect instead of overwriting')
    expected = shlex.join([str(root/'bin/python3.12'), '-u', '-m', 'lamda.services', '-module', 'fwd'])
    if current != expected: raise RuntimeError('Unrecognized existing forwarding command')
    from bk_fwd_service import NATIVE_SHA, CLIENT_SHA
    if hashlib.sha256((root/'bin/xfrpc').read_bytes()).hexdigest()!=NATIVE_SHA:
        raise RuntimeError('Native runtime integrity mismatch')
    if hashlib.sha256((user/'phone-pilot/frpc-0.72.0-bk.1-android-arm64').read_bytes()).hexdigest()!=CLIENT_SHA:
        raise RuntimeError('Recovery client integrity mismatch')
    files={}
    for name in ('bk_fwd_service.py','bk_frpc_adapter.py'):
        data=(Path(__file__).parent/name).read_bytes();compile(data,name,'exec')
        target=task/name
        if target.exists() or target.is_symlink():raise RuntimeError('Unowned forwarding source')
        atomic(target,data);files[name]=hashlib.sha256(data).hexdigest()
    manifest={'installed':False,'command':command,'previous_command':current,'files':files,
              'owner':'BK phone pilot','review_after':'2026-11-17'}
    atomic(manifest_path,json.dumps(manifest,indent=2).encode())
    # The native generator resets svc.fwd.command. BkRtcExtension applies this
    # manifest through Supervisor after generation on every service startup.
    manifest['installed']=True;atomic(manifest_path,json.dumps(manifest,indent=2).encode())
    print(json.dumps({'installed':True,'activation_required':True,'native_unchanged':True}))


if __name__=='__main__':main()
