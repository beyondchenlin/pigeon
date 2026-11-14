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
        if identity(pid) == start:
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
