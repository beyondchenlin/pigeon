"""Check an enrolled phone on its authenticated MQTT session or bundle change."""
import asyncio
import hashlib
import json
import logging
import shlex
import time
import uuid

from executor.config import db
from executor.models import Device, UserDevice, session_scope
from .bk_bridge_policy import enrolled
from .bk_device_channel import open_device
from .bk_native_coordinator import BUNDLE_DIR, WAITING, onboard
from .bk_native_reload import NativeReload

LOG = logging.getLogger(__name__)
TARGETS = {
    'bk_ai_phone.py': 'bk_ai_phone.py',
    'bk_ai_phone_mcp_candidate.py': 'bk_ai_phone.py',
    'bk_native_mcp.py': 'bk_native_mcp.py',
    'extension/bk_ai_guard_extension.py': 'bk_ai_guard_extension.py',
}

# Executed using the phone's own FIRERPA environment. A read never creates a
# manifest or imports phone code until all four installed files are verified.
INSPECT = """
import hashlib, json, os, sys
from pathlib import Path
user = Path(os.environ['USRDIR'])
modules = user / 'modules'
root = user / 'phone-pilot'
expected = json.loads(sys.argv[1])
def inspect():
    manifest = root / 'bk-native-bridge-install.json'
    if any(p.is_symlink() for p in (modules, modules/'extension', root, manifest)):
        raise ValueError('linked bridge path')
    m = json.loads(manifest.read_text()) if manifest.exists() else {}
    needs = 'current' if manifest.exists() else 'missing'
    for name, wanted in expected.items():
        path = modules / name
        if path.is_symlink():
            raise ValueError('linked bridge file')
        if not path.exists():
            needs = 'missing'
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        previous = m.get('files', {}).get(name)
        if actual not in (previous, wanted):
            raise ValueError('unowned bridge file')
        if (actual != wanted or previous != wanted) and needs == 'current':
            needs = 'outdated'
    if needs != 'current':
        return needs
    sys.path.insert(0, str(modules))
    from bk_ai_phone import live_guard_ready
    return 'current' if m.get('installed') is True and m.get('reload_needed') is False and live_guard_ready(root/'bk-ai') else 'pending_activation'
print(inspect())
"""


def desired_files():
    return {target: hashlib.sha256((BUNDLE_DIR / source).read_bytes()).hexdigest()
            for target, source in TARGETS.items()}


def installed_state(device, files):
    command = ('export LD_LIBRARY_PATH="$ROOTDIR/lib"; exec "$ROOTDIR/bin/python3" -B -c '
               + shlex.quote(INSPECT) + ' ' + shlex.quote(json.dumps(files)))
    reply = device.execute_script(command, timeout=10)
    state = reply.stdout.decode().strip()
    if reply.exitstatus or state not in ('current', 'missing', 'outdated', 'pending_activation'):
        raise RuntimeError('bridge_inspection_failed')
    return state


def inspect_phone(domain, files):
    device = open_device(domain, 15)
    try:
        return installed_state(device, files)
    finally:
        device.channel.close()


class BridgeUpdates:
    def __init__(self):
        self.checked = {}
        self.retry_at = {}

    def check(self, domain, owner, files):
        with session_scope() as session:
            device = (session.query(Device).join(UserDevice, UserDevice.device_id == Device.id)
                      .filter(Device.domain == domain, Device.is_deleted.is_(False),
                              UserDevice.user_id == owner).one_or_none())
            if not device or device.state != 'online':
                self.checked.pop(domain, None)
                return
            mqtt_session = db.get('ss:' + device.dev_id)
            if not mqtt_session:
                self.checked.pop(domain, None)
                return
            # FIRERPA can reconnect its FRP control link while keeping MQTT.
            # Pigeon assigns the new authenticated forward a new service port.
            route = device.service_port
            row = session.query(NativeReload).filter_by(device_id=device.id).one_or_none()
            pending = row is not None and row.phase in WAITING
        key = (owner, mqtt_session, route, tuple(files.items()))
        if self.checked.get(domain) == key and not pending:
            return
        if pending:
            result = onboard(domain, owner, str(uuid.uuid4()), bridge_only=True)
            state = result['state']
        else:
            state = inspect_phone(domain, files)
            LOG.info('Bridge inspection device=%s session=%s route=%s state=%s', domain,
                     hashlib.sha256(mqtt_session).hexdigest()[:10], route, state)
            if state == 'current':
                self.checked[domain] = key
                return
            result = onboard(domain, owner, str(uuid.uuid4()), bridge_only=True)
            state = result['state']
        LOG.info('Bridge update device=%s state=%s attempt=%s', domain, state, result.get('attempt_id'))
        # Re-inspect the actual files and live guard even after a successful
        # receipt. Never mark an ambiguous reload as current.
        self.retry_at[domain] = time.monotonic() + (5 if state in ('connected','waiting_reconnect') else 25)

    async def poll(self):
        # PeriodicCallback awaits this coroutine; phone I/O never blocks Tornado.
        devices = enrolled()
        for removed in self.checked.keys() - devices.keys():
            self.checked.pop(removed)
        if not devices:
            return
        files = desired_files()
        for domain, owner in devices.items():
            if time.monotonic() < self.retry_at.get(domain, 0):
                continue
            try:
                await asyncio.to_thread(self.check, domain, owner, files)
            except Exception as error:
                LOG.warning('Bridge inspection unavailable device=%s error=%s', domain, type(error).__name__)
                self.retry_at[domain] = time.monotonic() + 30
