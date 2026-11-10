"""Rollback the legacy BK forwarding replacement on an authorized shell device.

Stage the verified Android ARM64 release and bk_frpc_adapter.py first. No network
download, configuration/credential copying, root, or whole-service restart here.
Installation is withdrawn: replacing bin/xfrpc breaks FIRERPA's next cold start.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import signal
import subprocess
import time

VERSION = '0.72.0-bk.1'
BINARY_SHA256 = 'c31826e2f29b2efacf97577b15e7d7e5937ab5d4a72815343c22bb275c57432c'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic(path, content, mode=0o700):
    temp = path.with_name(path.name + '.bk-install')
    # Do not follow or overwrite an unexpected stale temporary/symlink.
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def process_ids(executable):
    result = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if proc.stat().st_uid == 2000 and proc.joinpath('exe').readlink() == executable:
                result.append(int(proc.name))
        except OSError:
            pass
    return result


def capture_process(executable):
    pids = process_ids(executable)
    if len(pids) != 1:
        raise RuntimeError('Expected exactly one owned forwarding process')
    stat = Path('/proc', str(pids[0]), 'exe').stat()
    return pids[0], stat.st_dev, stat.st_ino


def activate(identity, new_executable):
    pid, dev, inode = identity
    # Atomic replacement marks the running old executable as '(deleted)'.
    # Match its inode, not its now-replaced filename, before signalling.
    proc = Path('/proc', str(pid))
    stat = proc.joinpath('exe').stat()
    if proc.stat().st_uid != 2000 or (stat.st_dev, stat.st_ino) != (dev, inode):
        raise RuntimeError('Forwarding process changed; retry inspection')
    os.kill(pid, signal.SIGTERM)
    until = time.monotonic() + 40
    while time.monotonic() < until:
        active = process_ids(new_executable)
        if len(active) == 1 and active[0] != pid:
            return
        time.sleep(1)
    raise RuntimeError('FIRERPA did not respawn the forwarding child; rollback available')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', required=True)
    parser.add_argument('--user-root', required=True)
    parser.add_argument('--original-backup', help='Adopt a previously backed-up ELF from this same runtime')
    parser.add_argument('--activate', action='store_true', help='Replace only the supervised forwarding child')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    # Fail before touching the device, including its manifests and backups.
    # A live child-only switch passed prior tests but the native launcher later
    # rejects the replaced runtime file as "file broken". Keep rollback usable.
    if not args.rollback:
        raise RuntimeError('Installation withdrawn: replacing native xfrpc breaks FIRERPA startup; use --rollback')
    if os.getuid() != 2000 or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Requires authorized Android ARM64 shell UID 2000')
    root, usr = Path(args.runtime_root).resolve(), Path(args.user_root).resolve()
    boundary = Path('/data/local/tmp')
    if root == boundary or usr == boundary or not root.is_relative_to(boundary) or not usr.is_relative_to(boundary):
        raise RuntimeError('Runtime and user directories must be below /data/local/tmp')
    if not (root / 'bin/python3').is_file():
        raise RuntimeError('Not a FIRERPA runtime')
    task = usr / 'phone-pilot'
    task.mkdir(mode=0o700, exist_ok=True)
    if task.is_symlink() or not task.resolve().is_relative_to(usr):
        raise RuntimeError('Unexpected task directory link')
    entry = root / 'bin/xfrpc'
    if entry.is_symlink():
        raise RuntimeError('Unexpected client entry symlink')
    binary = task / ('frpc-' + VERSION + '-android-arm64')
    adapter = task / 'bk_frpc_adapter.py'
    manifest_path = task / 'bk-frpc-install.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['runtime_root'] != str(root):
            raise RuntimeError('Manifest belongs to another runtime')
        backup = Path(manifest['original_backup'])
        if sha(backup) != manifest['original_sha256']:
            raise RuntimeError('Original client backup was modified')
        if sha(entry) not in (manifest['original_sha256'], manifest.get('launcher_sha256')):
            raise RuntimeError('Runtime entry changed, possibly after an App upgrade; inspect first')
    else:
        if args.rollback:
            raise RuntimeError('No installation manifest; refusing to guess rollback')
        original = Path(args.original_backup).resolve() if args.original_backup else entry
        data = original.read_bytes()
        if not data.startswith(b'\x7fELF'):
            raise RuntimeError('Original client must be an ELF binary')
        digest = hashlib.sha256(data).hexdigest()
        backup = task / ('xfrpc-original-' + digest[:12])
        if not backup.exists():
            atomic(backup, data)
        elif sha(backup) != digest:
            raise RuntimeError('Existing backup does not match')
        manifest = dict(runtime_root=str(root), original_backup=str(backup),
                        original_sha256=digest, owner='BK phone pilot', review_after='2026-11-17')

    if args.rollback:
        identity = capture_process(binary) if args.activate else None
        atomic(entry, backup.read_bytes())
        if args.activate:
            activate(identity, entry)
        manifest['installed'] = False
    else:
        if sha(binary) != BINARY_SHA256 or not adapter.is_file():
            raise RuntimeError('Missing adapter or unverified BK binary')
        binary.chmod(0o700)
        config = usr / '.local/run/fwd.toml'
        result = subprocess.run([str(binary), 'verify', '-c', str(config)], capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError('Native forwarding configuration is not compatible')
        identity = None
        if args.activate:
            # An earlier BK release runs its own binary, not the xfrpc launcher.
            previous = task / ('frpc-' + manifest['version'] + '-android-arm64') if manifest.get('installed') else entry
            identity = capture_process(previous)
        launcher = ('#!/system/bin/sh\n# BK_OFFICIAL_FRPC_ADAPTER\nexec ' +
                    shlex.quote(str(root / 'bin/python3')) + ' ' + shlex.quote(str(adapter)) + ' "$@"\n')
        atomic(entry, launcher.encode())
        manifest.update(installed=True, version=VERSION, binary_sha256=BINARY_SHA256,
                        adapter_sha256=sha(adapter), launcher_sha256=sha(entry))
        # Save rollback metadata before activation, even if activation fails.
        atomic(manifest_path, json.dumps(manifest, indent=2).encode(), 0o600)
        if identity:
            activate(identity, binary)
    atomic(manifest_path, json.dumps(manifest, indent=2).encode(), 0o600)
    print(json.dumps({'installed':manifest['installed'], 'version':VERSION,
                      'forwarding_pids':process_ids(binary if manifest['installed'] else entry)}))


if __name__ == '__main__':
    main()
