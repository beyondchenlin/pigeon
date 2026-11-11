import json, os, re, inspect, shlex
from pathlib import Path
root=Path('/data/local/tmp/CnyL76/server'); user=Path('/data/local/tmp/usr')
assert os.getuid()==2000
parents=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        if os.readlink(p/'exe')!=str(root/'bin/python3.12'): continue
        if not re.search(r'^PPid:\s+1$',(p/'status').read_text(),re.M): continue
        env=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
        if b'PKGDIR' in env: parents.append((int(p.name),env))
    except OSError: pass
assert len(parents)==1
os.environ.update({k.decode():v.decode() for k,v in parents[0][1].items()})
from lamda.globals import config,properties,supervisor
for name,obj in (('config',config),('properties',properties)):
    methods={}
    for key in dir(obj):
        if key.startswith('_'): continue
        try:
            value=getattr(obj,key)
            if callable(value): methods[key]=str(inspect.signature(value))
        except (ValueError,TypeError): pass
    print(json.dumps({'object':name,'type':str(type(obj)),'methods':methods}))
safe=('adb.enable','adb.privileged','sshd.enable','tunnel2.enable','mdns.enable','mdns.meta','fwd.enable','cron.enable','openvpn.enable','gproxy.enable','frida.enable','enhanced-stealth-mode')
for name,obj in (('config',config),('properties',properties)):
    for key in safe:
        try:
            value=obj.get(key)
            print(json.dumps({'object':name,'key':key,'value':value}))
        except Exception as e: print(json.dumps({'object':name,'key':key,'error':type(e).__name__}))
sshd=supervisor.getProcessInfo('sshd')['pid']
children=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        st=(p/'status').read_text()
        if re.search(r'^PPid:\s+'+str(sshd)+r'$',st,re.M): children.append(int(p.name))
    except OSError: pass
print(json.dumps({'sshd_pid':sshd,'sshd_child_processes':children}))
cron=supervisor.getProcessInfo('cron')['pid']
args=[x.decode(errors='replace') for x in (Path('/proc')/str(cron)/'cmdline').read_bytes().split(b'\0') if x]
print(json.dumps({'cron_process_args':args}))
for directory in (user/'.local/cron',user/'.local/spool/cron',user/'cron',user/'crontabs',user/'.local/crontabs',user/'var/spool/cron'):
    if directory.is_dir(): print(json.dumps({'cron_directory':str(directory),'files':[{'name':p.name,'size':p.stat().st_size} for p in directory.iterdir()]}))
