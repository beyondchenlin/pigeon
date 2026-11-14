"""One-shot provisioning with durable exclusion and conservative recovery."""
import logging
import os
import time
from pathlib import Path
import uuid

from executor.models import Device, UserDevice, session_scope
from .bk_device_channel import open_device
from .bk_native_gateway_health import check_gateway
from .bk_native_gateway import remove_dead_worker_connections
from .bk_native_onboard import provision
from .bk_native_reload import NativeReload, reserve, begin_dispatch, observe

BUNDLE_DIR = Path(os.environ.get('BK_NATIVE_BUNDLE_DIR', '/user/phone-pilot/bk-native-bundle'))
WAITING = frozenset(('reserved', 'dispatching', 'awaiting_reconnect', 'unknown'))


def _identity(session, domain, user_id):
    device = session.query(Device).filter_by(domain=domain).with_for_update().one_or_none()
    if (device is None or device.is_deleted or not session.query(UserDevice.id)
            .filter_by(device_id=device.id, user_id=user_id).first()):
        raise ValueError('device_not_authorized')
    return device


def _phase(session, device_id, attempt_id):
    row = session.query(NativeReload).filter_by(device_id=device_id).with_for_update().one()
    if row.attempt_id != attempt_id:
        raise ValueError('attempt_mismatch')
    return row.phase


def _not_sent(device_id, attempt_id):
    with session_scope() as session:
        if _phase(session, device_id, attempt_id) == 'reserved':
            observe(session, device_id, attempt_id, 'not_sent')


def _record(device_id, attempt_id, outcome):
    with session_scope() as session:
        phase = _phase(session, device_id, attempt_id)
        if phase == 'ready':
            return 'connected'
        observe(session, device_id, attempt_id, outcome)
        return 'connected' if outcome == 'verified' else 'waiting_reconnect'


def _reconcile(domain, user_id, device_id, attempt_id):
    """Only inspect and confirm; never submit a second reload."""
    try:
        with session_scope() as session:
            _identity(session, domain, user_id)
            phase = _phase(session, device_id, attempt_id)
        if phase == 'ready':
            return {'state': 'connected', 'attempt_id': attempt_id}
        if phase not in ('dispatching', 'awaiting_reconnect', 'unknown'):
            return {'state': 'waiting', 'attempt_id': attempt_id}
        device = open_device(domain, 10)
        locked = False
        try:
            locked = bool(device._acquire_lock(leaseTime=300))
            if not locked:
                return {'state': 'waiting_reconnect', 'attempt_id': attempt_id}
            device.device_info()
            result = provision(device, BUNDLE_DIR)
        finally:
            if locked:
                try: device._release_lock()
                except Exception: pass
            device.channel.close()
        if result.get('state') == 'connected':
            return {'state': _record(device_id, attempt_id, 'verified'), 'attempt_id': attempt_id}
    except Exception:
        # No retry of reload and no release based on a lost/ambiguous response.
        logging.warning('Native guard reconciliation remains unconfirmed')
    return {'state': 'waiting_reconnect', 'attempt_id': attempt_id}


def onboard(domain, user_id, request_id):
    try:
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid_request') from None
    with session_scope() as session:
        device = _identity(session, domain, user_id)
        device_id = device.id
        existing = session.query(NativeReload).filter_by(device_id=device_id).with_for_update().one_or_none()
        if existing and existing.phase in WAITING:
            if existing.user_id != user_id:
                raise ValueError('attempt_not_authorized')
            old_id, phase = existing.attempt_id, existing.phase
        elif existing and existing.attempt_id == request_id:
            return {'state': 'connected' if existing.phase == 'ready' else 'not_ready', 'attempt_id': request_id}
        else:
            old_id, phase = None, None
    if old_id:
        return (_reconcile(domain, user_id, device_id, old_id)
                if phase != 'reserved' else {'state': 'install_unconfirmed', 'attempt_id': old_id})
    if not check_gateway():
        return {'state': 'gateway_unavailable'}
    try:
        with session_scope() as session:
            _identity(session, domain, user_id)
            remove_dead_worker_connections(session, device_id)
            _, created = reserve(session, device_id, user_id, request_id)
        if not created:
            return {'state': 'waiting', 'attempt_id': request_id}
    except ValueError as error:
        if str(error) in ('device_has_gateway_connection', 'device_unavailable', 'device_busy', 'reload_unconfirmed'):
            return {'state': 'waiting_for_idle'}
        raise
    try:
        device = open_device(domain, 10)
    except Exception:
        _not_sent(device_id, request_id)
        return {'state': 'not_ready', 'attempt_id': request_id}
    locked = False
    dispatched = False
    try:
        started = time.monotonic()
        locked = bool(device._acquire_lock(leaseTime=300))
        if not locked:
            _not_sent(device_id, request_id)
            return {'state': 'waiting_for_idle', 'attempt_id': request_id}
        device.device_info()
        result = provision(device, BUNDLE_DIR, request_id=uuid.UUID(request_id).hex, start_app=True)
        state = result.get('state')
        if state != 'reload_required':
            _not_sent(device_id, request_id)
            return {'state': state if state in ('connected', 'not_installed', 'not_ready', 'auth_failed', 'incompatible') else 'not_ready',
                    'attempt_id': request_id}
        if not check_gateway():
            _not_sent(device_id, request_id)
            return {'state': 'gateway_unavailable', 'attempt_id': request_id}
        if time.monotonic() - started >= 240:
            _not_sent(device_id, request_id)
            return {'state': 'waiting_for_idle', 'attempt_id': request_id}
        with session_scope() as session:
            current = _identity(session, domain, user_id)
            if current.state != 'online' or current.controlling or not check_gateway():
                observe(session, device_id, request_id, 'not_sent')
                return {'state': 'waiting_for_idle', 'attempt_id': request_id}
            if not begin_dispatch(session, device_id, request_id):
                return {'state': 'waiting', 'attempt_id': request_id}
        dispatched = True  # committed before the only non-retrying RPC
        try:
            accepted = bool(device.reload(clean=False))
        except Exception:
            accepted = False
        outcome = 'accepted' if accepted else 'unknown'
        return {'state': _record(device_id, request_id, outcome), 'attempt_id': request_id}
    except Exception:
        # An uncertain installation is not replayed and leaves admission shut.
        logging.warning('Native onboarding attempt is unconfirmed')
        if dispatched:
            try: _record(device_id, request_id, 'unknown')
            except Exception: pass
            return {'state': 'waiting_reconnect', 'attempt_id': request_id}
        return {'state': 'install_unconfirmed', 'attempt_id': request_id}
    finally:
        if locked:
            try: device._release_lock()
            except Exception: pass
        device.channel.close()
