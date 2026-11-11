"""Disable unused optional services without reloading the main phone runtime."""
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

root = Path('/data/local/tmp/CnyL76/server')
user = Path('/data/local/tmp/usr')
assert os.getuid() == 2000
assert user.resolve() == user and root.resolve() == root
parents = []
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        if os.readlink(p/'exe') != str(root/'bin/python3.12'): continue
        if not re.search(r'^PPid:\s+1$', (p/'status').read_text(), re.M): continue
        env = dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
        if env.get(b'PKGDIR') == str(root/'lib/python3.12/site-packages/lamda').encode():
            parents.append((int(p.name),env))
    except OSError: pass
assert len(parents) == 1
main_pid, env = parents[0]
os.environ.update({k.decode():v.decode() for k,v in env.items()})
from lamda.globals import config, supervisor

targets = ('sshd','cron')
settings = {key:'false' for key in ('sshd.enable','cron.enable','tunnel2.enable',
                                    'mdns.enable','mdns.meta','adb.enable')}
runtime_settings = {'svc.'+k:v for k,v in settings.items()}
runtime_settings.update({'svc.sshd.autostart':'false','svc.cron.autostart':'false'})
before_processes = {p['name']:{k:p[k] for k in ('pid','start','statename')} for p in supervisor.getAllProcessInfo()}
assert before_processes['fwd']['statename'] == 'RUNNING'
assert before_processes['adb']['statename'] == 'STOPPED'
assert config.get('svc.mdns.enable') == 'false'
assert all(not v for v in supervisor.reloadConfig()[0]), 'Unrelated Supervisor changes are pending'
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        status=(p/'status').read_text()
        if any(re.search(r'^PPid:\s+'+str(before_processes[n]['pid'])+r'$',status,re.M) for n in targets):
            raise RuntimeError('An optional service has an active child; no changes applied')
    except FileNotFoundError: pass
cron_path = Path(config.get('svc.cron.path'))
assert cron_path == user/'.local/job'
assert cron_path.is_dir() and not cron_path.is_symlink()
cron_entries=list(cron_path.iterdir())
assert all(p.name=='.cronstamps' and p.is_dir() and not p.is_symlink()
           and not list(p.iterdir()) for p in cron_entries), 'Cron jobs exist; do not disable scheduling'

props = user/'.local/properties'
paths = {'properties':props, **{n:user/'.local/run/services'/n for n in targets}}
assert all(p.is_file() and not p.is_symlink() for p in paths.values())
original = {name:p.read_bytes() for name,p in paths.items()}
section = 'phone-pilot-minimal-services-20261111'
assert ('['+section+']').encode() not in original['properties'], 'Task already applied; inspect instead of repeating'
before_runtime = {k:config.get(k) for k in runtime_settings}
task = user/'phone-pilot/service-minimization-20261111'
assert not task.exists() and not task.is_symlink()
assert task.parent.resolve() == user/'phone-pilot'
task.mkdir(mode=0o700)

def atomic(path, data):
    assert path.parent.resolve().is_relative_to(user)
    assert not path.is_symlink()
    fd, temporary = tempfile.mkstemp(prefix='.minimal-services-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chmod(temporary,0o600)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

for name,data in original.items(): atomic(task/(name+'.before'),data)
manifest = {'owner':'Current phone-pilot task user','review_after':'2026-11-18',
            'created_utc':datetime.now(timezone.utc).isoformat(),'main_pid':main_pid,
            'before_processes':before_processes,'before_runtime':before_runtime,
            'settings':settings,'sha256_before':{k:hashlib.sha256(v).hexdigest() for k,v in original.items()},
            'state':'prepared','no_ui_tests':True}
atomic(task/'receipt.json',json.dumps(manifest,indent=2).encode())

try:
    # Preserve every existing byte and add only a final, narrowly scoped section.
    addition='\n['+section+']\n'+''.join(k+'=false\n' for k in settings)
    atomic(props,original['properties']+addition.encode())
    for name in targets:
        parsed=configparser.ConfigParser(interpolation=None)
        parsed.read_string(original[name].decode())
        assert parsed.sections()==['program:'+name]
        parsed['program:'+name]['autostart']='false'
        output=io.StringIO();parsed.write(output)
        atomic(paths[name],output.getvalue().encode())
    for key,value in runtime_settings.items(): config.set(key,value)
    added,changed,removed=supervisor.reloadConfig()[0]
    assert not added and not removed and set(changed)<=set(targets), 'Unexpected pending service changes'
    for name in targets:
        if supervisor.getProcessInfo(name)['statename'] not in ('STOPPED','EXITED','FATAL'):
            supervisor.stopProcess(name,True)
        if name in changed:
            supervisor.removeProcessGroup(name)
            supervisor.addProcessGroup(name)
    after_processes={p['name']:{k:p[k] for k in ('pid','start','statename')} for p in supervisor.getAllProcessInfo()}
    assert all(after_processes[n]['statename']=='STOPPED' for n in targets)
    assert all(after_processes[n]==v for n,v in before_processes.items() if n not in targets), 'An unrelated process changed'
    assert all(config.get(k)==v for k,v in runtime_settings.items())
    persisted=configparser.ConfigParser(interpolation=None);persisted.read(props)
    assert all(persisted[section][k]==v for k,v in settings.items())
    manifest.update(state='applied',after_processes=after_processes,
                    after_runtime={k:config.get(k) for k in runtime_settings},
                    bytes_retained=sum(p.stat().st_size for p in task.iterdir()))
    atomic(task/'receipt.json',json.dumps(manifest,indent=2).encode())
    print(json.dumps({'state':'applied','settings':settings,'stopped':list(targets),
                      'main_pid':main_pid,'unrelated_processes_unchanged':True,
                      'recovery_directory':str(task),'no_ui_tests':True}))
except BaseException as error:
    # Roll back only this task's three configuration files and optional processes.
    rollback_errors=[]
    for name,path in paths.items():
        try: atomic(path,original[name])
        except Exception as e: rollback_errors.append(type(e).__name__)
    for key,value in before_runtime.items():
        try: config.set(key,value)
        except Exception as e: rollback_errors.append(type(e).__name__)
    try:
        added,changed,removed=supervisor.reloadConfig()[0]
        if added or removed or set(changed)-set(targets): raise RuntimeError('Unrelated rollback changes')
        for name in targets:
            if name in changed:
                if supervisor.getProcessInfo(name)['statename'] not in ('STOPPED','EXITED','FATAL'):
                    supervisor.stopProcess(name,True)
                supervisor.removeProcessGroup(name);supervisor.addProcessGroup(name)
            if before_processes[name]['statename']=='RUNNING' and supervisor.getProcessInfo(name)['statename']=='STOPPED':
                supervisor.startProcess(name,True)
    except Exception as e: rollback_errors.append(type(e).__name__)
    manifest.update(state='rollback-incomplete' if rollback_errors else 'rolled-back',
                    error=type(error).__name__,rollback_errors=rollback_errors)
    atomic(task/'receipt.json',json.dumps(manifest,indent=2).encode())
    print(json.dumps({'state':manifest['state'],'error':type(error).__name__,
                      'rollback_errors':rollback_errors,'recovery_directory':str(task)}))
    raise SystemExit(1)
