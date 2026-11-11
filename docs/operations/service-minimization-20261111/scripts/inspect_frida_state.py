"""Read existing version/configuration/process metadata; do not start Frida."""
import importlib.metadata
import json
import os
from pathlib import Path
import re

root=Path('/data/local/tmp/CnyL76/server')
user=Path('/data/local/tmp/usr')
assert os.getuid()==2000
parents=[]
frida_processes=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        exe=os.readlink(p/'exe')
        comm=(p/'comm').read_text().strip()
        if 'frida' in exe.lower() or 'frida' in comm.lower():
            frida_processes.append({'pid':int(p.name),'exe':exe,'comm':comm})
        if exe!=str(root/'bin/python3.12'): continue
        status=(p/'status').read_text()
        if not re.search(r'^PPid:\s+1$',status,re.M): continue
        env=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
        if env.get(b'PKGDIR')==str(root/'lib/python3.12/site-packages/lamda').encode():
            parents.append((int(p.name),env))
    except OSError: pass
assert len(parents)==1
pid,env=parents[0]
os.environ.update({k.decode():v.decode() for k,v in env.items()})
from lamda.globals import config,properties,supervisor
import lamda
report={'uid':os.getuid(),'main_pid':pid,'module_version':getattr(lamda,'__version__',None),
        'frida_processes_visible_to_shell':frida_processes}
report['package_versions']={}
for name in ('lamda','frida','frida-tools'):
    try: report['package_versions'][name]=importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: report['package_versions'][name]=None
report['persisted']={key:properties.query_last(key,default='UNSET') for key in
                     ('enhanced-stealth-mode','frida.version','frida.enable')}
report['runtime']={key:config.get(key) for key in
                   ('svc.frida.enable','svc.frida.version')}
report['stealth_settings']={}
for key in config.wildcard('*stealth*'):
    value=config.get(key)
    if value in ('true','false','','0','1',True,False,None):
        report['stealth_settings'][key]=value
    else: report['stealth_settings'][key]='non-boolean value withheld'
report['managed_processes']=[{k:p[k] for k in ('name','pid','statename')} for p in supervisor.getAllProcessInfo()]
report['bundled_frida_files']=[{'name':p.name,'bytes':p.stat().st_size} for p in (root/'bin').glob('*frida*') if p.is_file()]
report['version_files']={}
for p in (root/'version',root/'VERSION',root/'.version'):
    if p.is_file() and p.stat().st_size<256:
        value=p.read_text().strip()
        if re.fullmatch(r'[A-Za-z0-9._+\-\s]{1,200}',value):report['version_files'][p.name]=value
print(json.dumps(report,ensure_ascii=False,indent=2))
