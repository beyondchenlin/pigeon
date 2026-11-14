"""On-demand phone maintenance switch, invoked over the existing FIRERPA RPC.

This command exits after each request. Off leaves no new phone watcher/worker.
Never starts FIRERPA, enables Android debugging or modifies phone properties.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from bk_maintenance_mode import read_mode, write_json
from bk_lifecycle_monitor import NATIVE_HASH, Proc, start_monitor
from bk_android_spawn import spawn_pipes
from bk_service_lifecycle import launch_environment

USER = Path(__file__).resolve().parent.parent
RUNTIME = Path(sys.executable).resolve().parent.parent
ROOT = USER / 'phone-pilot/lifecycle'
TERMUX = '/data/data/com.termux/files'
PILOT = TERMUX + '/home/phone-pilot'
HELPER = PILOT + '/start.sh'


def termux_environment(parent):
    allowed = ('ANDROID_ROOT', 'ANDROID_DATA', 'ANDROID_ART_ROOT', 'ANDROID_I18N_ROOT',
               'ANDROID_TZDATA_ROOT', 'BOOTCLASSPATH', 'DEX2OATBOOTCLASSPATH')
    env = {key: parent[key] for key in allowed if key in parent}
    if not env.get('BOOTCLASSPATH') or not env.get('DEX2OATBOOTCLASSPATH'):
        raise ValueError('Android runtime environment unavailable')
    env.update(HOME=TERMUX+'/home', PREFIX=TERMUX+'/usr',
               PATH=TERMUX+'/usr/bin:/system/bin', TMPDIR=TERMUX+'/usr/tmp')
    return env


def run_as(args, check=True):
    result = subprocess.run(['/system/bin/run-as', 'com.termux']+args,
                            capture_output=True, timeout=4)
    if check:
        result.check_returncode()
    return result


def helper_identity():
    result = run_as(['/system/bin/cat', PILOT+'/tunnel.pid'], check=False)
    if result.returncode:
        return None
    pid = int(result.stdout)
    stat = run_as(['/system/bin/cat', '/proc/%d/stat'%pid], check=False)
    if stat.returncode:
        return None
    fields = stat.stdout.decode().rsplit(')', 1)[1].split()
    if fields[0] == 'Z':
        return None
    cmd = run_as(['/system/bin/cat', '/proc/%d/cmdline'%pid]).stdout.split(b'\0')
    if HELPER.encode() not in cmd or TERMUX.encode()+b'/usr/bin/bash' not in cmd:
        return None
    return pid, fields[19]


def monitor_identity():
    try:
        saved = json.loads((ROOT/'status.json').read_text())
        pid = int(saved['monitor'])
        proc = Proc()
        # A saved PID can be reused by an unrelated Android app. Check its
        # owner before reading /proc/PID/exe, which shell cannot inspect for
        # another app. A later permission error still remains unknown.
        if (proc.root / str(pid)).stat().st_uid != 2000:
            return None
        identity = proc.identity(pid)
    except (FileNotFoundError, ProcessLookupError, KeyError):
        return None
    cmd = (Path('/proc')/str(identity.pid)/'cmdline').read_bytes().split(b'\0')
    expected = str(USER/'modules/bk_lifecycle_monitor.py').encode()
    if identity.uid != 2000 or identity.exe != str((RUNTIME/'bin/python3').resolve()) or expected not in cmd:
        return None
    return identity


def status():
    mode = read_mode(USER)
    try:
        receipt = json.loads((ROOT/'maintenance-result.json').read_text())
    except (FileNotFoundError, ValueError):
        receipt = {}
    return dict(mode, phase=receipt.get('phase', 'not_applied'),
                receipt_id=receipt.get('request_id'), error=receipt.get('error'),
                monitor_running=monitor_identity() is not None,
                helper_running=helper_identity() is not None)


def stop_monitor():
    identity = monitor_identity()
    if identity is None:
        return
    fd = os.pidfd_open(identity.pid)
    try:
        if Proc().same(identity) and monitor_identity() == identity:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
    finally:
        os.close(fd)


def apply_mode(mode):
    # The main service identity is checked but is never signalled by this tool.
    if os.getuid() != 2000 or hashlib.sha256((RUNTIME/'lib/python3.12/site-packages/lamda/web/bundle.cpython-312.so').read_bytes()).hexdigest() != NATIVE_HASH:
        raise ValueError('Unverified runtime')
    if mode['enabled']:
        parent = Proc().identity(int((USER/'lamda.pid').read_text()))
        if parent.uid != 2000 or parent.exe != str((RUNTIME/'bin/python3').resolve()):
            raise ValueError('Unverified main process')
        env = termux_environment(os.environ)
        run_as(['/system/bin/sh', '-c', 'umask 077; test -d '+PILOT+' && test ! -L '+PILOT+'/maintenance.enabled && : > '+PILOT+'/maintenance.enabled'])
        if monitor_identity() is None:
            start_monitor(str(RUNTIME), str(USER), parent.pid)
        if helper_identity() is None:
            command = 'exec >/dev/null 2>&1; exec "$PREFIX/bin/setsid" "$PREFIX/bin/bash" "$HOME/phone-pilot/start.sh"'
            pid, out, err = spawn_pipes(['/system/bin/run-as','com.termux','/system/bin/env','-i']+
                [key+'='+value for key,value in env.items()]+[TERMUX+'/usr/bin/bash','-c',command])
            os.close(out); os.close(err)
    else:
        run_as(['/system/bin/rm', '-f', PILOT+'/maintenance.enabled'])
        stop_monitor()
        original = helper_identity()
        if original and helper_identity() == original:
            run_as(['/system/bin/kill', '-TERM', str(original[0])])
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        if read_mode(USER)['request_id'] != mode['request_id']:
            raise ValueError('Request superseded')
        observed = status()
        if observed['monitor_running'] == mode['enabled'] and observed['helper_running'] == mode['enabled']:
            if not mode['enabled']:
                env = termux_environment(os.environ)
                run_as(['/system/bin/env','-i']+[key+'='+value for key,value in env.items()]+[TERMUX+'/usr/bin/termux-wake-unlock'])
            return
        time.sleep(.25)
    raise TimeoutError('Maintenance transition did not settle')


def worker(request_id):
    import fcntl
    lock = (ROOT/'maintenance-worker.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Fresh single-threaded interpreter only; no gRPC/Python at-fork hooks.
    if os.fork():
        os._exit(0)
    os.setsid()
    null = os.open('/dev/null', os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    os.close(null)
    mode = read_mode(USER)
    if mode['request_id'] != request_id:
        return
    receipt = dict(request_id=request_id, phase='applying', at=time.time(), worker=os.getpid())
    write_json(ROOT/'maintenance-result.json', receipt)
    try:
        apply_mode(mode)
        receipt.update(phase='done', at=time.time())
    except Exception as error:
        receipt.update(phase='failed', at=time.time(), error=type(error).__name__)
    write_json(ROOT/'maintenance-result.json', receipt)


def set_mode(value):
    import fcntl
    if set(value) != {'action','enabled','request_id'} or value['action'] != 'set' or type(value['enabled']) is not bool:
        raise ValueError('Invalid switch request')
    request_id = value['request_id']
    if str(uuid.UUID(request_id)) != request_id:
        raise ValueError('Invalid request identity')
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (ROOT/'maintenance-request.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = read_mode(USER)
        if old['request_id'] == request_id:
            if old['enabled'] != value['enabled']:
                raise ValueError('Request identity reused')
            return status()
        try:
            receipt = json.loads((ROOT/'maintenance-result.json').read_text())
        except (FileNotFoundError, ValueError):
            receipt = {}
        if receipt.get('phase') == 'applying' and time.time()-receipt.get('at',0)<60:
            raise RuntimeError('Another transition is pending')
        write_json(ROOT/'maintenance.json', dict(enabled=value['enabled'], request_id=request_id, at=time.time()))
        write_json(ROOT/'maintenance-result.json', dict(request_id=request_id, phase='applying', at=time.time()))
        pid, out, err = spawn_pipes([str(RUNTIME/'bin/python3'),str(Path(__file__).resolve()),'--worker',request_id],
                                    environment=launch_environment(os.environ,str(RUNTIME),str(USER)))
        os.close(out); os.close(err)
        return status()


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--worker':
        worker(sys.argv[2]); return
    value = json.loads(base64.b64decode(sys.argv[1], validate=True))
    if value == {'action':'status'}:
        result = status()
    else:
        result = set_mode(value)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
