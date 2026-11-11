"""Recover only positively identified pre-exec stalls, without restarting FIRERPA.

Runs outside the threaded/gRPC service. Never reads credentials, tests network
availability, clears caches, or signals the main service. Missing /proc access
or pidfd support disables intervention instead of weakening the checks.
"""
from dataclasses import dataclass
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import time

NATIVE_HASH = '03841b518f9a1308de3af62ae2fddbf4b5c0b918fa4e4aa701c5771a720fd05f'
FUTEX_WAITS = frozenset(('futex_wait_queue', 'futex_wait_queue_me'))


@dataclass(frozen=True)
class Identity:
    pid: int
    ticks: str
    uid: int
    exe: str
    ppid: int


class Proc:
    def __init__(self, root='/proc'):
        self.root = Path(root)

    def identity(self, pid):
        p = self.root / str(pid)
        st = (p / 'stat').read_text().rsplit(')', 1)[1].split()
        if st[0] == 'Z':
            raise ProcessLookupError(pid)
        return Identity(pid, st[19], p.stat().st_uid, os.readlink(p / 'exe'), int(st[1]))

    def same(self, identity):
        try:
            return self.identity(identity.pid) == identity
        except (OSError, ValueError, IndexError):
            return False

    def children(self, parent):
        result = set()
        try:
            for t in (self.root / str(parent.pid) / 'task').iterdir():
                result.update(map(int, (t / 'children').read_text().split()))
            return result
        except FileNotFoundError:
            # Android kernels commonly omit CONFIG_CHECKPOINT_RESTORE's
            # task/children file. Enumerate UID/PPID, with the same identity
            # checks applied again before considering or signalling a child.
            for p in self.root.iterdir():
                if not p.name.isdecimal():
                    continue
                try:
                    if p.stat().st_uid != parent.uid:
                        continue
                    child = self.identity(int(p.name))
                    if child.ppid == parent.pid:
                        result.add(child.pid)
                except (OSError, ValueError, IndexError):
                    continue
        return result

    def pipes(self, pid):
        result = {}
        p = self.root / str(pid)
        for fd in (p / 'fd').iterdir():
            try:
                target = os.readlink(fd)
                if not target.startswith('pipe:['):
                    continue
                info = dict(line.split(':', 1) for line in (p / 'fdinfo' / fd.name).read_text().splitlines())
                flags = int(info['flags'].strip(), 8)
                if flags & os.O_CLOEXEC:
                    result[int(fd.name)] = (target, flags & os.O_ACCMODE)
            except (OSError, KeyError, ValueError):
                continue
        return result

    def blocked_reads(self, pid):
        result = set()
        # Linux x86_64 read=0; Android arm64 read=63. No other syscall accepted.
        for t in (self.root / str(pid) / 'task').iterdir():
            if (t / 'wchan').read_text().strip() != 'pipe_read':
                continue
            fields = (t / 'syscall').read_text().split()
            if len(fields) > 1 and fields[0] in ('0', '63'):
                result.add(int(fields[1], 0))
        return result

    def candidate(self, parent, child_pid, min_age=20):
        """Match the historical exec-error-pipe + inherited Python stall."""
        child = self.identity(child_pid)
        if child.ppid != parent.pid or child.uid != parent.uid or child.exe != parent.exe:
            return None
        age = float((self.root / 'uptime').read_text().split()[0]) - int(child.ticks) / os.sysconf('SC_CLK_TCK')
        if age < min_age:
            return None
        p, c = self.root / str(parent.pid), self.root / str(child.pid)
        if (c / 'cmdline').read_bytes() != (p / 'cmdline').read_bytes():
            return None
        waits = [(t / 'wchan').read_text().strip() for t in (c / 'task').iterdir()]
        if not 1 <= len(waits) <= 2 or not all(w in FUTEX_WAITS for w in waits):
            return None
        parent_pipes, child_pipes = self.pipes(parent.pid), self.pipes(child.pid)
        for fd in self.blocked_reads(parent.pid):
            pipe = parent_pipes.get(fd)
            if not pipe or pipe[1] != os.O_RDONLY:
                continue
            # The parent has closed its error writer; the child alone retains
            # the close-on-exec writer while the parent waits for exec or exit.
            if any(v == (pipe[0], os.O_WRONLY) for v in parent_pipes.values()):
                continue
            if any(v == (pipe[0], os.O_WRONLY) for v in child_pipes.values()):
                if self.same(parent) and self.same(child):
                    return child
        return None

    def candidates(self, parent, min_age=20):
        found = []
        for pid in self.children(parent):
            try:
                item = self.candidate(parent, pid, min_age)
                if item:
                    found.append(item)
            except (OSError, ValueError, IndexError, KeyError):
                continue
        return found


def release(proc, parent, child, grace=2):
    """A pidfd pins the exact task; a reused PID can never receive the signal."""
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        return 'pidfd_unavailable'
    try:
        fd = os.pidfd_open(child.pid)
    except OSError:
        return 'pidfd_unavailable'
    try:
        if not proc.same(parent) or proc.candidate(parent, child.pid) != child:
            return 'changed'
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        end = time.monotonic() + grace
        while time.monotonic() < end:
            if not proc.same(child):
                return 'released_term'
            time.sleep(.1)
        if not proc.same(parent) or proc.candidate(parent, child.pid) != child:
            return 'changed_after_term'
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        return 'released_kill'
    except ProcessLookupError:
        return 'already_exited'
    finally:
        os.close(fd)


def start_monitor(runtime, user, parent_pid=None):
    """Called by the extension; C-only spawn avoids the defective fork path."""
    from bk_android_spawn import spawn_pipes
    from bk_service_lifecycle import launch_environment
    env = launch_environment(os.environ, runtime, user)
    pid, out, err = spawn_pipes([runtime + '/bin/python3', user + '/modules/bk_lifecycle_monitor.py',
                                 '--watch', runtime, user, str(os.getpid() if parent_pid is None else parent_pid)], environment=env)
    os.close(out)
    os.close(err)
    return pid


def port_query(root):
    """Enable only a read transaction verified against this phone's framework.

    No guessed Binder transaction numbers and no pairing/debugging mutations.
    This optional pilot channel writes only a numeric discovery receipt into
    the existing Termux helper; its SSH private key stays inside Termux.
    """
    path = root / 'port-query.json'
    if not path.exists():
        return None
    config = json.loads(path.read_text())
    framework = Path('/system/framework/framework.jar')
    if config.get('method') != 'TRANSACTION_getAdbWirelessPort' or type(config.get('transaction')) is not int:
        raise ValueError('Unverified ADB query')
    if hashlib.sha256(framework.read_bytes()).hexdigest() != config.get('framework_sha256'):
        raise ValueError('Android framework changed')
    return config['transaction']


def publish_adb_port(transaction):
    import re
    r = subprocess.run(['/system/bin/service','call','adb',str(transaction)],
                       capture_output=True, text=True, timeout=3)
    match = re.search(r'Parcel\(\s*00000000\s+([0-9a-f]{8})', r.stdout)
    if r.returncode != 0 or match is None:
        return False
    port = int(match[1], 16)
    if not 1024 <= port <= 65535:
        port = -1  # Wireless debugging is unavailable; never enable it here.
    command = ('umask 077; cd /data/data/com.termux/files/home/phone-pilot || exit 1; '
               'test ! -L adb-discovered-port || exit 1; '
               'cat > .discovery-$$.tmp && chmod 600 .discovery-$$.tmp && '
               'mv .discovery-$$.tmp adb-discovered-port')
    r = subprocess.run(['/system/bin/run-as','com.termux','/system/bin/sh','-c',command],
                       input='%d %d\n'%(time.time(),port),capture_output=True,text=True,timeout=3)
    return r.returncode == 0


def watch(runtime, user, parent_pid):
    import fcntl
    from bk_maintenance_mode import enabled
    runtime, user = Path(runtime).resolve(), Path(user).resolve()
    if not enabled(user):
        return
    assert os.getuid() == 2000
    assert hashlib.sha256((runtime / 'lib/python3.12/site-packages/lamda/web/bundle.cpython-312.so').read_bytes()).hexdigest() == NATIVE_HASH
    root = user / 'phone-pilot/lifecycle'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError('Unexpected monitor directory link')
    lock = (root / 'monitor.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return
    # This is a fresh, single-threaded interpreter, never the gRPC host. Reparent
    # the monitor so the native shutdown path cannot wait for its own observer.
    if os.fork():
        os._exit(0)
    os.setsid()
    null = os.open('/dev/null', os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    if null > 2:
        os.close(null)
    logger = logging.getLogger('bk.lifecycle')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(root / 'events.jsonl', maxBytes=1024*1024, backupCount=2)
    logger.addHandler(handler)
    proc = Proc()
    parent = proc.identity(parent_pid)
    expected_exe = str((runtime / 'bin/python3').resolve())
    assert parent.uid == 2000 and parent.exe == expected_exe
    observed, interventions = set(), []
    next_discovery = 0
    def report(event, **fields):
        logger.info(json.dumps(dict(at=time.time(), event=event, **fields)))
    try:
        transaction = port_query(root)
    except (OSError, ValueError):
        transaction = None
        report('adb_query_configuration_unavailable')
    report('started', monitor=os.getpid(), parent=parent.pid, parent_ticks=parent.ticks)
    try:
        while True:
            if not enabled(user):
                break
            if not proc.same(parent):
                # Follow a replacement only if the official pidfile identifies
                # the same verified runtime. Never start or revive a service.
                try:
                    new = proc.identity(int((user / 'lamda.pid').read_text()))
                    if new.uid != 2000 or new.exe != expected_exe:
                        break
                    parent = new
                    observed.clear()
                except (OSError, ValueError, IndexError):
                    break
            now = time.monotonic()
            interventions = [t for t in interventions if now-t < 600]
            try:
                if transaction is not None and now >= next_discovery:
                    try:
                        published = publish_adb_port(transaction)
                    except (OSError, subprocess.TimeoutExpired):
                        published = False
                    if not published:
                        report('adb_discovery_unavailable')
                    next_discovery = now + 15
                found = set(proc.candidates(parent))
                for child in sorted(found & observed, key=lambda c:c.pid):
                    if len(interventions) >= 3:
                        continue
                    outcome = release(proc, parent, child)
                    report(outcome, parent=parent.pid, child=child.pid, child_ticks=child.ticks)
                    interventions.append(now)
                observed = found
                state = dict(at=time.time(), monitor=os.getpid(), parent=parent.pid, parent_ticks=parent.ticks,
                             candidates=len(found), interventions_10m=len(interventions), circuit_open=len(interventions)>=3,
                             adb_port_discovery=transaction is not None)
                temp = root / 'status.tmp'
                temp.write_text(json.dumps(state))
                temp.chmod(0o600)
                os.replace(temp, root / 'status.json')
            except (OSError, ValueError, IndexError):
                observed.clear()
                report('observation_unavailable')
            time.sleep(5)
    finally:
        report('stopped', parent=parent.pid)
        handler.close()
        lock.close()


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 5 or sys.argv[1] != '--watch':
        raise SystemExit('Use the installed lifecycle extension')
    watch(sys.argv[2], sys.argv[3], int(sys.argv[4]))
