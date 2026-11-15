"""Prepare the native bridge through one authenticated FIRERPA connection.

The caller must check device ownership and scheduling state before invoking
this function.  It does not restart a device or change Android permissions.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import time
import uuid

FILES = ('install_bk_native_bridge.py', 'bk_ai_phone.py', 'bk_native_mcp.py',
         'bk_ai_guard_extension.py')
MAX_REPLY = 8192


class OnboardError(Exception):
    pass


class PreInstallError(OnboardError):
    """The bridge installer was never invoked; no reload was dispatched."""
    pass


def _result(reply):
    if reply.exitstatus != 0 or len(reply.stdout) > MAX_REPLY:
        raise OnboardError('phone_command_failed')
    try:
        body = json.loads(reply.stdout)
        if not isinstance(body, dict):
            raise ValueError()
        return body
    except (TypeError, ValueError, UnicodeError):
        raise OnboardError('invalid_phone_reply') from None


def _python_command(script, args):
    return 'export PYTHONDONTWRITEBYTECODE=1 LD_LIBRARY_PATH="$ROOTDIR/lib"; exec "$ROOTDIR/bin/python3" ' + shlex.join([script, *args])


def confirm_bridge(device, bundle):
    """Confirm the installed release in place, without uploading or installing it again."""
    source = (Path(bundle) / 'install_bk_native_bridge.py').read_text()
    # Use the trusted server installer and check against the desired release
    # as well as the phone's ownership manifest. Do not execute a stale bridge.
    checks = {n: hashlib.sha256((Path(bundle) / n).read_bytes()).hexdigest() for n in FILES[1:]}
    check = """
user = Path(os.environ['USRDIR'])
modules = user / 'modules'
for source, targets in FILES.items():
    for name in targets:
        path = modules / name
        if path.is_symlink() or digest(path.read_bytes()) != expected[source]:
            raise ValueError('wrong installed bridge')
print(json.dumps(confirm_activation(user, modules)))
"""
    script = ("__name__ = 'installer'\nexpected = " + repr(checks) + "\n" + source + "\n" + check)
    reply = device.execute_script(_python_command('-c', [script]), timeout=15)
    if reply.exitstatus != 0:
        return {'state': 'waiting_reconnect'}
    value = _result(reply)
    return {'state': 'connected' if value.get('restart_required') is False and value.get('installed') is True else 'waiting_reconnect'}


def provision(device, bundle_dir, *, request_id=None, start_app=False, confirm_guard=False,
              bridge_only=False, discover_release=False):
    """Stage and validate one bundle. A live guard reload is confirmed separately."""
    bundle = Path(bundle_dir)
    if bridge_only and confirm_guard:
        return confirm_bridge(device, bundle)
    files = {}
    for name in FILES:
        source = bundle / name
        if source.is_symlink() or not source.is_file():
            raise OnboardError('bundle_unavailable')
        files[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    identity = request_id or uuid.uuid4().hex
    if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise OnboardError('invalid_request')
    stage = '/data/local/tmp/bk-native-onboard-' + identity
    # mkdir, not mkdir -p: a prior uncertain attempt must not be reused.
    try:
        result = device.execute_script('umask 077; mkdir ' + shlex.quote(stage), timeout=10)
    except Exception as error:
        raise PreInstallError('staging_unavailable') from error
    if result.exitstatus != 0:
        raise PreInstallError('staging_unavailable')
    complete = False
    installer_started = False
    try:
        for name in FILES:
            device.upload_file(str(bundle / name), stage + '/' + name)
        if not bridge_only:
            if discover_release or os.environ.get('BK_CLAW_RELEASE_PACKAGE_ENABLED') == '1':
                probe_code = ("import sys,json;sys.path.insert(0,sys.argv[1]);from bk_native_mcp import discover;"
                              "print(json.dumps(discover()))")
            else:
                probe_code = ("import sys,json;sys.path.insert(0,sys.argv[1]);from bk_native_mcp import probe;"
                              "print(json.dumps(probe('/sdcard/Android/data/com.beyondchenlin.bk101.nativemcp/files/mcp-usb.json')))")
            cmd = ('export PYTHONDONTWRITEBYTECODE=1 LD_LIBRARY_PATH="$ROOTDIR/lib"; exec "$ROOTDIR/bin/python3" -c '
                   + shlex.quote(probe_code) + ' ' + shlex.quote(stage))
            probe = _result(device.execute_script(cmd, timeout=15))
            if probe.get('state') == 'not_ready' and start_app:
                # Opening the installed app starts its normal service; it does not
                # change the user's MCP switch or permissions. Never replay an
                # uncertain launch and never open the app during reconciliation.
                package = probe.get('package', 'com.beyondchenlin.bk101.nativemcp')
                if package not in ('com.beyondchenlin.bk101.nativemcp', 'com.beyondchenlin.bkclaw'):
                    raise OnboardError('invalid_package')
                opened = device.execute_script(
                    'am start -n ' + package + '/com.wangzan101.mika.ui.MainActivity', timeout=10)
                if opened.exitstatus == 0:
                    time.sleep(2)
                    probe = _result(device.execute_script(cmd, timeout=15))
            if probe.get('state') != 'connected':
                confirmed = False
                if confirm_guard:
                    reply = device.execute_script(
                        _python_command(stage + '/install_bk_native_bridge.py', ['--confirm']), timeout=15)
                    if reply.exitstatus == 0:
                        value = _result(reply)
                        confirmed = value.get('installed') is True and value.get('restart_required') is False
                complete = True
                result = {'state': probe.get('state') if probe.get('state') in
                          ('not_installed', 'not_ready', 'auth_failed', 'incompatible') else 'not_ready'}
                if confirmed: result['guard_confirmed'] = True
                return result
        installer_started = True
        installed = _result(device.execute_script(
            _python_command(stage + '/install_bk_native_bridge.py', []), timeout=25))
        if installed.get('installed') is not True or type(installed.get('restart_required')) is not bool:
            raise OnboardError('invalid_install_result')
        # An earlier installation may have been followed by a reload.  Always
        # ask the installer to verify the current guard and installed hashes.
        confirm_reply = device.execute_script(
            _python_command(stage + '/install_bk_native_bridge.py', ['--confirm']), timeout=15)
        complete = True
        if confirm_reply.exitstatus != 0:
            return {'state': 'reload_required'}
        confirmation = _result(confirm_reply)
        return {'state': 'connected' if confirmation.get('restart_required') is False else 'reload_required'}
    except Exception as error:
        # Failures before invoking the installer cannot have changed the guard.
        # Preserve the staged files and allow the caller to release only this reservation.
        if not installer_started and not confirm_guard:
            raise PreInstallError('pre_install_failed') from error
        raise
    finally:
        if complete:
            # Delete only unchanged, task-owned files, and only if the stage
            # contains exactly the four files we created. Never recursively rm.
            code = ("import sys,os,json,hashlib; p=sys.argv[1]; expected=json.loads(sys.argv[2]); "
                    "assert os.path.realpath(p)==p and p.startswith('/data/local/tmp/bk-native-onboard-'); "
                    "assert set(os.listdir(p))==set(expected); "
                    "assert all(not os.path.islink(os.path.join(p,n)) and "
                    "hashlib.sha256(open(os.path.join(p,n),'rb').read()).hexdigest()==h for n,h in expected.items()); "
                    "[os.unlink(os.path.join(p,n)) for n in expected]; os.rmdir(p)")
            cmd = ('export LD_LIBRARY_PATH="$ROOTDIR/lib"; "$ROOTDIR/bin/python3" -c '
                   + shlex.quote(code) + ' ' + shlex.quote(stage) + ' ' + shlex.quote(json.dumps(files)))
            try:
                device.execute_script(cmd, timeout=10)
            except Exception:
                pass  # An unconfirmed stage is retained for inspection.
