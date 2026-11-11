import json,os
from pathlib import Path
base=Path('/data/local/tmp/usr/.local/job')
assert os.getuid()==2000
assert base.resolve()==base
rows=[]
for p in base.rglob('*'):
    if len(rows)>=30:break
    row={'relative_path':str(p.relative_to(base)),'directory':p.is_dir(),'symlink':p.is_symlink(),'bytes':p.lstat().st_size}
    if p.is_file() and not p.is_symlink() and p.stat().st_size<8192:
        row['active_line_count']=sum(bool(s.strip()) and not s.lstrip().startswith('#') for s in p.read_text(errors='replace').splitlines())
    rows.append(row)
print(json.dumps({'cron_entries':rows}))
