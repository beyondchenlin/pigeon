"""Reapply the owned forwarding command using Supervisor's reload API."""
import configparser
import hashlib
import io
import json
from pathlib import Path
import tempfile
import os


def configure(supervisor, user_root, restore=False):
    user=Path(user_root).resolve();base=user/'phone-pilot/bk-forwarding'
    manifest=json.loads((base/'install.json').read_text())
    if not manifest.get('installed') and not restore:return 'disabled'
    for name,digest in manifest['files'].items():
        if name not in ('bk_fwd_service.py','bk_frpc_adapter.py'):raise RuntimeError('Unexpected forwarding file')
        path=base/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Changed forwarding installation')
    path=user/'.local/run/services/fwd'
    if path.is_symlink():raise RuntimeError('Unexpected service configuration link')
    before=path.read_bytes();config=configparser.ConfigParser(interpolation=None);config.read_string(before.decode())
    section=config['program:fwd']
    if section['command'] not in (manifest['previous_command'],manifest['command']):
        raise RuntimeError('Unowned forwarding command')
    section['command']=manifest['previous_command'] if restore else manifest['command']
    out=io.StringIO();config.write(out)
    def write(data):
        fd,name=tempfile.mkstemp(prefix='.bk-fwd-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            os.chmod(name,0o600);os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
    write(out.getvalue().encode())
    try:
        added,changed,removed=supervisor.reloadConfig()[0]
        if added or removed or set(changed)-{'fwd'}:raise RuntimeError('Other service changes pending')
        if 'fwd' in changed:
            info=supervisor.getProcessInfo('fwd')
            if info['statename'] not in ('STOPPED','EXITED','FATAL'):supervisor.stopProcess('fwd',True)
            supervisor.removeProcessGroup('fwd');supervisor.addProcessGroup('fwd')
        return 'configured'
    except BaseException:
        write(before)
        # Stage the original config again. Do not touch unrelated services.
        try:
            _,changes,_=supervisor.reloadConfig()[0]
            if set(changes)=={'fwd'}:
                info=supervisor.getProcessInfo('fwd')
                if info['statename'] not in ('STOPPED','EXITED','FATAL'):supervisor.stopProcess('fwd',True)
                supervisor.removeProcessGroup('fwd');supervisor.addProcessGroup('fwd')
        except Exception:pass
        raise
