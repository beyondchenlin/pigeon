import os,json,re,importlib,inspect,dis
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
pkg=root/'lib/python3.12/site-packages/lamda'
for directory in (pkg/'web',pkg/'services'):
    print(json.dumps({'directory':str(directory),'files':[p.name for p in directory.iterdir()]}))
from lamda.globals import config,supervisor
before={p['name']:(p['pid'],p['start']) for p in supervisor.getAllProcessInfo()}
for path in (pkg/'web').glob('*tunnel*'):
    name=path.name.split('.')[0]
    module=importlib.import_module('lamda.web.'+name)
    for key,obj in vars(module).items():
        if inspect.isclass(obj) and obj.__module__==module.__name__:
            for method,func in vars(obj).items():
                if hasattr(func,'__code__'):
                    text='\n'.join(str(i.argval) for i in dis.get_instructions(func))
                    print(json.dumps({'class':key,'method':method,'mentions_enable':'enable' in text,'names':list(func.__code__.co_names),'config_constants':[v for v in func.__code__.co_consts if isinstance(v,str) and (v.startswith('svc.') or v.startswith('tunnel2'))]}))
print(json.dumps({'process_snapshot_unchanged':before=={p['name']:(p['pid'],p['start']) for p in supervisor.getAllProcessInfo()}}))
