"""Stage and dispatch the receipt-backed Android service restart once."""
import hashlib
import json
from pathlib import Path
import re
import shlex

FILES = ('bk_native_restart_once.py', 'bk_service_lifecycle.py')


def prepare(device, bundle_dir, attempt, *, reuse=False):
    if not isinstance(attempt, str) or not re.fullmatch(r'[a-f0-9]{32}', attempt):
        raise ValueError('invalid attempt')
    bundle = Path(bundle_dir)
    checksums = {}
    for name in FILES:
        source = bundle / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('restart source unavailable')
        checksums[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    stage = '/data/local/tmp/bk-native-restart-' + attempt
    if not reuse:
        result = device.execute_script('umask 077; mkdir ' + shlex.quote(stage), timeout=10)
        if result.exitstatus != 0:
            raise ValueError('restart stage unavailable')
        for name in FILES:
            device.upload_file(str(bundle / name), stage + '/' + name)
    code = ("import hashlib,os,pathlib,sys,json; p=pathlib.Path(sys.argv[1]); expected=json.loads(sys.argv[2]); "
            "assert not p.is_symlink() and all(not (p/n).is_symlink() and "
            "hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in expected.items()); "
            "r=pathlib.Path(os.environ['USRDIR'])/'phone-pilot'/'guard-restart'/(sys.argv[3]+'.json'); "
            "assert not r.exists() and not r.is_symlink() and not r.with_suffix('.claim').exists(); "
            "os.execv(sys.executable,[sys.executable,str(p/'bk_native_restart_once.py'),sys.argv[3],*sys.argv[4:]])")
    prefix = ('export PYTHONDONTWRITEBYTECODE=1 LD_LIBRARY_PATH="$ROOTDIR/lib"; '
              'exec "$ROOTDIR/bin/python3" -c ' + shlex.quote(code) + ' ' + shlex.quote(stage)
              + ' ' + shlex.quote(json.dumps(checksums)) + ' ' + attempt)
    result = device.execute_script(prefix + ' --check', timeout=10)
    if result.exitstatus != 0:
        raise ValueError('restart check failed')
    try:
        if json.loads(result.stdout).get('state') != 'ready': raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('restart check failed') from None
    # This exact command is executed at most once after the server commits the
    # durable dispatch marker. Stage and phone receipt remain for recovery.
    return prefix


def dispatch(device, command, attempt):
    result = device.execute_script(command, timeout=12)
    if result.exitstatus != 0:
        return False
    try:
        body = json.loads(result.stdout)
        return body == {'state': 'prepared', 'attempt': attempt}
    except (ValueError, TypeError):
        return False
