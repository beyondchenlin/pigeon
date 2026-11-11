import configparser
import json
import os
from pathlib import Path
import re

user = Path('/data/local/tmp/usr')
runtime = Path('/data/local/tmp/CnyL76/server')
assert os.getuid() == 2000
run = user / '.local/run'
for directory in (run, run/'services', runtime/'etc'):
    if directory.is_dir():
        print(json.dumps({'directory':str(directory),'entries':[{'name':p.name,'directory':p.is_dir()} for p in directory.iterdir()]}))
environment = (run/'environment').read_text()
print(json.dumps({'environment_line_prefixes':[re.split(r'[=:\s]',line.strip(),1)[0] for line in environment.splitlines() if line.strip()]}))
for p in (run/'services').iterdir():
    if not p.is_file(): continue
    config = configparser.ConfigParser(interpolation=None)
    config.read(p)
    safe = ('autostart','autorestart','numprocs','process_name','stopasgroup','killasgroup')
    print(json.dumps({'service_config':p.name,'sections':{s:{k:config[s][k] for k in safe if k in config[s]} for s in config.sections()}}))
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        status = (p/'status').read_text()
        uid = re.search(r'^Uid:\s+(\d+)',status,re.M)
        if not uid or int(uid[1]) != 2000: continue
        comm = (p/'comm').read_text().strip()
        exe = os.readlink(p/'exe')
        print(json.dumps({'process':int(p.name),'comm':comm,'exe':exe,'ppid':re.search(r'^PPid:\s+(\d+)',status,re.M)[1]}))
        if 'python' in exe:
            pairs = (p/'environ').read_bytes().split(b'\0')
            env = dict(x.split(b'=',1) for x in pairs if b'=' in x)
            safe_env = ('ROOTDIR','USRDIR','PKGDIR','PORT','SUPERVISOR_SERVER_URL','SUPERVISOR_PROCESS_NAME','SUPERVISOR_GROUP_NAME')
            values = {k:env[k.encode()].decode(errors='replace') for k in safe_env if k.encode() in env}
            if 'SUPERVISOR_SERVER_URL' in values:
                values['SUPERVISOR_SERVER_URL']=re.sub(r'//[^/@]+@','//[redacted]@',values['SUPERVISOR_SERVER_URL'])
            print(json.dumps({'process_environment':int(p.name),'values':values}))
    except (OSError,ValueError,AttributeError): pass
