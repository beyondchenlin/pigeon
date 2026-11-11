import json,os
from pathlib import Path
base=Path('/data/local/tmp/usr/phone-pilot/service-minimization-20261111')
assert os.getuid()==2000 and base.resolve()==base
receipt=json.loads((base/'receipt.json').read_text())
print(json.dumps({'state':receipt['state'],'review_after':receipt['review_after'],
                  'files':[{'name':p.name,'bytes':p.stat().st_size,'mode':oct(p.stat().st_mode & 0o777)} for p in base.iterdir()],
                  'bytes_total':sum(p.stat().st_size for p in base.iterdir()),
                  'after_runtime':receipt['after_runtime'],
                  'stopped':{n:receipt['after_processes'][n]['statename'] for n in ('sshd','cron')},
                  'main_pid':receipt['main_pid']}))
