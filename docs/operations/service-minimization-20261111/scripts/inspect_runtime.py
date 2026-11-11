import json
import os
from pathlib import Path
import re
import configparser

root=Path('/data/local/tmp/CnyL76/server')
user=Path('/data/local/tmp/usr')
assert os.getuid()==2000
matches=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        if os.readlink(p/'exe')!=str(root/'bin/python3.12'): continue
        if not re.search(r'^PPid:\s+1$',(p/'status').read_text(),re.M): continue
        env=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
        if env.get(b'PKGDIR')!=str(root/'lib/python3.12/site-packages/lamda').encode(): continue
        matches.append((int(p.name),env))
    except OSError: continue
assert len(matches)==1
pid,env=matches[0]
os.environ.update({k.decode():v.decode() for k,v in env.items()})
import lamda.globals as globals_
supervisor=globals_.supervisor
print(json.dumps({'main_pid':pid,'supervisor_processes':[{k:v[k] for k in ('name','group','statename','pid','start')} for v in supervisor.getAllProcessInfo()]}))
print(json.dumps({'global_names':[k for k in vars(globals_) if not k.startswith('_')]}))
import lamda.services as services
print(json.dumps({'manager_type':str(type(services.manager)),'manager_methods':[k for k in dir(services.manager) if not k.startswith('_')]}))
for name in ('sshd','adb','mdns','cron','tunnel2'):
    try:
        obj=services.manager.get(name)
        print(json.dumps({'service':name,'type':str(type(obj)),'methods':[k for k in dir(obj) if not k.startswith('_')]}))
    except Exception as e:
        print(json.dumps({'service':name,'lookup_error':type(e).__name__}))
sshconfig=configparser.ConfigParser(interpolation=None)
text=(user/'.local/run/sshd.config').read_text()
allowed=('Port','ListenAddress','PidFile','AllowUsers','PasswordAuthentication','PubkeyAuthentication')
print(json.dumps({'sshd_settings':[line for line in text.splitlines() if line.strip() and line.split()[0] in allowed]}))
crontabs=user/'.local/var/spool/cron'
print(json.dumps({'cron_candidates':[str(p) for p in user.glob('**/crontab')][:10]}))
