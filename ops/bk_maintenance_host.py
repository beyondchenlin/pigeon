"""One-shot systemd path handler; no polling or listener while mode is off."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from bk_adb_keepalive import request, state_of, AdbProtocolError
from bk_maintenance_mode import write_json

ROOT = Path('/opt/phone-pilot-20261108/pigeon/data/user/phone-pilot/maintenance/umdax0nurk9o')
SOCKET = '/opt/phone-pilot-20261108/stf/data/adb-socket/adb.sock'
SERVICE = 'phone-pilot-adb-maintenance.service'


def apply(root=ROOT, systemctl=None, adb=None):
    systemctl = systemctl or (lambda *args: subprocess.run(['systemctl',*args],capture_output=True,text=True,timeout=12))
    adb = adb or (lambda command: request(SOCKET,command))
    try:
        mode = json.loads((root/'host-request.json').read_text())
        if type(mode['enabled']) is not bool or str(uuid.UUID(mode['request_id'])) != mode['request_id']:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        mode = {'enabled':False,'request_id':None}
    marker = root/'host.enabled'
    receipt = dict(mode, at=time.time(), error=None)
    try:
        if mode['enabled']:
            write_json(marker, mode)
            systemctl('start',SERVICE).check_returncode()
        else:
            marker.unlink(missing_ok=True)
            systemctl('stop',SERVICE).check_returncode()
            try:
                if state_of(adb('host:devices')) != 'missing':
                    adb('host:disconnect:127.0.0.1:15555')
                receipt['transport_disconnected'] = state_of(adb('host:devices')) == 'missing'
            except FileNotFoundError:
                receipt['transport_disconnected'] = True
        receipt['active'] = systemctl('is-active',SERVICE).stdout.strip() == 'active'
    except (OSError, AdbProtocolError, subprocess.SubprocessError) as error:
        receipt.update(error=type(error).__name__, active=None)
    write_json(root/'host-status.json', receipt)
    return receipt


if __name__=='__main__':
    apply()
