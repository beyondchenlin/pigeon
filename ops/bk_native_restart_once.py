"""One-shot, receipt-backed FIRERPA restart after a guard installation.

Run only as the authorized Android shell. A detached worker checks the exact
official PID and guard identity, sends one graceful exit signal, and launches
the official service once only after that process has exited. Never replay an
existing request ID, including after a lost response.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

from bk_service_lifecycle import launch_environment, launch_official


def identity(pid, proc=Path('/proc')):
    try:
        return (proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None
    except (OSError, IndexError):
        raise ValueError('process identity unavailable') from None


def verify_user(user, boundary=Path('/data/local/tmp'), uid=None):
    if (os.getuid() if uid is None else uid) != 2000:
        raise ValueError('authorized shell required')
    user, boundary = Path(user).resolve(), Path(boundary).resolve()
    if user == boundary or not user.is_relative_to(boundary):
        raise ValueError('invalid user directory')
    return user


def inspect_guard(user, proc=Path('/proc'), uid=2000):
    root = user / 'phone-pilot' / 'bk-ai'
    if root.is_symlink() or (root / 'state.json').is_symlink():
        raise ValueError('linked guard state')
    state = json.loads((root / 'state.json').read_text())
    pid, start = state.get('guard_pid'), state.get('guard_identity')
    if pid is None and start is None and not state.get('guard_protocol'):
        # First installation has no prior guard. Pin the official live service
        # by its shell-owned PID, exact interpreter and mapped FIRERPA library.
        manifest = user / 'phone-pilot' / 'bk-native-bridge-install.json'
        if manifest.is_symlink():
            raise ValueError('linked install manifest')
        installed = json.loads(manifest.read_text())
        if (installed.get('reload_needed') is not True or installed.get('installed') is not True
                or installed.get('prior_guard') != {'pid': None, 'identity': None}
                or state.get('ai') or state.get('manual')):
            raise ValueError('first installation unconfirmed')
        pidfile = user / 'lamda.pid'
        if pidfile.is_symlink():
            raise ValueError('linked official PID')
        pid = int(pidfile.read_text().strip())
        process = proc / str(pid)
        runtime = Path(os.environ['ROOTDIR']).resolve()
        library = runtime / 'lib/python3.12/site-packages/lamda/web/bundle.cpython-312.so'
        if (pid <= 1 or pid == os.getpid() or process.stat().st_uid != uid
                or (process / 'exe').resolve() != (runtime / 'bin/python3').resolve()
                or (process / 'cmdline').read_bytes().split(b'\x00', 1)[0] != b'lamda'
                or not any(line.endswith(' ' + str(library)) for line in (process / 'maps').read_text().splitlines())):
            raise ValueError('unverified first-install process')
        start = identity(pid, proc)
        if not start or not start.isdecimal():
            raise ValueError('official process disappeared')
        return pid, start
    if (type(pid) is not int or pid <= 1 or pid == os.getpid() or not isinstance(start, str)
            or not start.isdecimal() or state.get('ai') or state.get('manual')
            or state.get('guard_protocol') != {'version': 2, 'pid': pid, 'identity': start}):
        raise ValueError('guard not idle or invalid')
    pidfile = user / 'lamda.pid'
    if pidfile.is_symlink() or int(pidfile.read_text().strip()) != pid:
        raise ValueError('official PID mismatch')
    if (proc / str(pid)).stat().st_uid != uid or identity(pid, proc) != start:
        raise ValueError('guard process changed')
    return pid, start


def write_receipt(path, body):
    fd, name = tempfile.mkstemp(prefix='.guard-restart-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(body, f); f.flush(); os.fsync(f.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def release_shutdown_stall(user, runtime, pid, start):
    """After a confirmed shutdown request, unblock only a proven pre-exec child."""
    from bk_lifecycle_monitor import Proc, release
    if (user / 'lamda.pid').exists():
        return False
    proc = Proc()
    parent = proc.identity(pid)
    if parent.uid != os.getuid() or parent.ticks != start or parent.exe != str((runtime / 'bin/python3').resolve()):
        return False
    matches = proc.candidates(parent)
    if len(matches) != 1:
        return False
    time.sleep(1)
    if (user / 'lamda.pid').exists() or not proc.same(parent):
        return False
    return release(proc, parent, matches[0]) in ('released_term', 'released_kill', 'already_exited')


def worker(user, runtime, attempt, expected_pid, expected_start):
    receipt = user / 'phone-pilot' / 'guard-restart' / (attempt + '.json')
    claim = receipt.with_suffix('.claim')
    try:
        fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return  # an earlier worker may already have sent the signal
    os.close(fd)
    body = {'attempt': attempt, 'state': 'prepared'}
    may_have_signalled = False
    try:
        time.sleep(3)  # allow the dispatch reply to leave the old service
        lock = user / 'phone-pilot' / 'bk-ai' / 'lock'
        if lock.is_symlink(): raise ValueError('linked guard lock')
        with lock.open('r') as guard_lock:
            fcntl.flock(guard_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            pid, start = inspect_guard(user)
            if (pid, start) != (expected_pid, expected_start):
                raise ValueError('guard changed')
            body['state'] = 'signalling'; write_receipt(receipt, body)
            may_have_signalled = True
            os.kill(pid, signal.SIGUSR2)  # exactly once
        body['state'] = 'waiting_exit'; write_receipt(receipt, body)
        deadline = time.monotonic() + 40
        while identity(pid) == start and time.monotonic() < deadline:
            time.sleep(1)
        still_running = identity(pid) == start
        if still_running:
            try:
                released = release_shutdown_stall(user, runtime, pid, start)
            except (OSError, ValueError, ImportError):
                released = False
            if released:
                deadline = time.monotonic() + 15
                while identity(pid) == start and time.monotonic() < deadline:
                    time.sleep(.5)
                still_running = identity(pid) == start
        if still_running:
            body['state'] = 'exit_unconfirmed'; write_receipt(receipt, body); return
        pidfile = user / 'lamda.pid'
        if pidfile.is_file():
            try: replacement = int(pidfile.read_text().strip())
            except (OSError, ValueError): replacement = 0
            if replacement != pid and replacement > 1 and identity(replacement):
                body['state'] = 'replacement_present'; write_receipt(receipt, body); return
        launcher = runtime / 'bin' / 'launch.sh'
        if launcher.is_symlink() or not launcher.is_file(): raise ValueError('launcher unavailable')
        body['state'] = 'launching'; write_receipt(receipt, body)
        with open(os.devnull, 'wb') as out:
            child = launch_official(os.environ, str(runtime), str(user), out)
            try: result = child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                body['state'] = 'launch_unconfirmed'; write_receipt(receipt, body); return
        body['state'] = 'launched' if result == 0 else 'launch_failed'
        write_receipt(receipt, body)
    except Exception:
        body['state'] = 'unknown' if may_have_signalled else 'failed'
        write_receipt(receipt, body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('attempt')
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--pid', type=int)
    parser.add_argument('--start')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-f0-9]{32}', args.attempt): raise ValueError('invalid attempt')
    user = verify_user(os.environ['USRDIR'])
    runtime = Path(os.environ['ROOTDIR']).resolve()
    if runtime == Path('/data/local/tmp') or not runtime.is_relative_to(Path('/data/local/tmp')):
        raise ValueError('invalid runtime')
    root = user / 'phone-pilot' / 'guard-restart'
    if (user / 'phone-pilot').is_symlink() or root.is_symlink(): raise ValueError('linked receipt directory')
    if args.check:
        inspect_guard(user)
        launcher = runtime / 'bin' / 'launch.sh'
        if launcher.is_symlink() or not launcher.is_file(): raise ValueError('launcher unavailable')
        print(json.dumps({'state':'ready'}))
        return
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt = root / (args.attempt + '.json')
    if args.worker:
        if not receipt.is_file() or receipt.is_symlink(): raise ValueError('missing receipt')
        worker(user, runtime, args.attempt, args.pid, args.start)
        return
    pid, start = inspect_guard(user)
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'attempt': args.attempt, 'state': 'prepared'}, f); f.flush(); os.fsync(f.fileno())
    env = launch_environment(os.environ, str(runtime), str(user))
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), args.attempt,
                      '--worker', '--pid', str(pid), '--start', start], env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, start_new_session=True)
    print(json.dumps({'state': 'prepared', 'attempt': args.attempt}))


if __name__ == '__main__': main()
