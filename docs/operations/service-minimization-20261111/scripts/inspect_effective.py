import json,os,re,configparser,shlex
from pathlib import Path
root=Path('/data/local/tmp/CnyL76/server');user=Path('/data/local/tmp/usr')
assert os.getuid()==2000
envs=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    try:
        if os.readlink(p/'exe')!=str(root/'bin/python3.12'):continue
        if not re.search(r'^PPid:\s+1$',(p/'status').read_text(),re.M):continue
        e=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
        if b'PKGDIR' in e:envs.append(e)
    except OSError:pass
assert len(envs)==1
os.environ.update({k.decode():v.decode() for k,v in envs[0].items()})
from lamda.globals import properties,config,supervisor
safe=('adb.enable','adb.privileged','sshd.enable','tunnel2.enable','mdns.enable','mdns.meta','fwd.enable','cron.enable','openvpn.enable','gproxy.enable','frida.enable','enhanced-stealth-mode')
for key in safe:
    print(json.dumps({'property':key,'persisted':properties.query_last(key,default='UNSET'),'effective':config.get('svc.'+key)}))
for name in ('cron','sshd','mdns','adb'):
    c=configparser.ConfigParser(interpolation=None); c.read(user/'.local/run/services'/name)
    for section in c.sections():
        command=c[section].get('command','')
        if name=='cron': print(json.dumps({'cron_service_command':command}))
print(json.dumps({'user_directories':[p.name for p in user.iterdir() if p.is_dir()]}))
for folder in (user/'.local',user/'etc',user/'var',user/'spool'):
    if folder.is_dir(): print(json.dumps({'directory':str(folder),'entries':[{'name':p.name,'directory':p.is_dir(),'size':p.stat().st_size} for p in folder.iterdir()]}))
# Read only names of in-memory settings; never print their values.
for pattern in ('*cron*','*sshd*','*tunnel2*','*mdns*'):
    try:
        result=config.wildcard(pattern)
        print(json.dumps({'config_pattern':pattern,'keys':result}))
    except Exception as e:print(json.dumps({'pattern':pattern,'error':type(e).__name__}))
cron_path=config.get('svc.cron.path')
print(json.dumps({'cron_path':cron_path}))
if cron_path:
    p=Path(cron_path)
    if p.is_dir():
        rows=[]
        for entry in p.iterdir():
            if entry.is_file():
                rows.append({'name':entry.name,'bytes':entry.stat().st_size,'active_lines':sum(bool(s.strip()) and not s.lstrip().startswith('#') for s in entry.read_text().splitlines())})
        print(json.dumps({'cron_files':rows}))
