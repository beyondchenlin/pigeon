"""Install the narrowly scoped BK RTC extension; no automatic process restart.

Stage this file and the three bk_* source files together. Installation keeps a
manifest and refuses to replace unowned files. --rollback removes only files
whose hash still matches this installation, preserving all other extensions.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

FILES = {'bk_rtc_core.py': 'bk_rtc_core.py', 'bk_android_spawn.py': 'bk_android_spawn.py',
         'bk_rtc_extension.py': 'extension/bk_rtc_extension.py'}

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def atomic(path, data):
    fd, temp = tempfile.mkstemp(prefix='.bk-rtc-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temp, 0o600); os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', required=True)
    parser.add_argument('--user-root', required=True)
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if os.getuid() != 2000: raise RuntimeError('Requires authorized Android shell UID 2000')
    root, user = Path(args.runtime_root).resolve(), Path(args.user_root).resolve()
    boundary = Path('/data/local/tmp')
    if any(p == boundary or not p.is_relative_to(boundary) for p in (root, user)):
        raise RuntimeError('Unexpected runtime/user directory')
    expected = {'webrtc.cpython-312.so': '4d4fc99598b3ef034151b65e45227a3990ff3c68735228303ab6fea71f750fe6',
                'web/bundle.cpython-312.so': '03841b518f9a1308de3af62ae2fddbf4b5c0b918fa4e4aa701c5771a720fd05f'}
    if not args.rollback:
        for name, digest in expected.items():
            if sha(root/'lib/python3.12/site-packages/lamda'/name) != digest:
                raise RuntimeError('Unverified native runtime')
    modules, task = user/'modules', user/'phone-pilot'
    for directory in (modules, modules/'extension', task):
        if directory.is_symlink(): raise RuntimeError('Unexpected module directory link')
        directory.mkdir(mode=0o700, exist_ok=True)
    manifest_path = task/'bk-rtc-install.json'
    if manifest_path.is_symlink(): raise RuntimeError('Unexpected manifest link')
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'files': {}}
    targets = []
    for source, relative in FILES.items():
        target = modules/relative
        if target.is_symlink(): raise RuntimeError('Unexpected extension link')
        if target.exists() and sha(target) != manifest['files'].get(relative):
            raise RuntimeError('Unowned or changed extension file: '+relative)
        targets.append((Path(__file__).parent/source, target, relative))
    if args.rollback:
        if not manifest_path.exists(): raise RuntimeError('No known installation')
        for _, target, _ in targets:
            if target.exists(): target.unlink()
        manifest.update(installed=False)
    else:
        for source, target, relative in targets:
            data = source.read_bytes(); compile(data, str(source), 'exec')
        # Record each completed write; ordinary failures restore prior files.
        # A hard interruption between file and manifest requires inspection.
        previous = {relative: target.read_bytes() if target.exists() else None for _, target, relative in targets}
        old_manifest = json.loads(json.dumps(manifest))
        try:
            for source, target, relative in targets:
                data = source.read_bytes()
                atomic(target, data); manifest['files'][relative] = sha(target)
                atomic(manifest_path, json.dumps(manifest, indent=2).encode())
        except BaseException:
            for _, target, relative in targets:
                data = previous[relative]
                if data is None:
                    if target.exists(): target.unlink()
                else: atomic(target, data)
            atomic(manifest_path, json.dumps(old_manifest, indent=2).encode())
            raise
        manifest.update(installed=True, version='1.0.0', runtime=str(root), user=str(user), owner='BK phone pilot', review_after='2026-11-17')
    atomic(manifest_path, json.dumps(manifest, indent=2).encode())
    print(json.dumps({'installed': manifest['installed'], 'files': list(manifest['files']), 'restart_required': True}))

if __name__ == '__main__': main()
