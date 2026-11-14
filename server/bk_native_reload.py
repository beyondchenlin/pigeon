"""Durable one-shot reload receipts; never retries a possibly sent reload.

This module only records intent and observations. The caller must arrange a
device-wide admission barrier before sending the external reload request.
"""
import time
import uuid
from sqlalchemy import Column, Float, ForeignKey, Integer, String
from executor.models import Base, Device, Task, UserDevice
from .bk_ai_store import AiTask, TERMINAL


class NativeReload(Base):
    __tablename__ = 'bk_native_reload'
    device_id = Column(Integer, ForeignKey('device.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    attempt_id = Column(String(36), nullable=False, unique=True)
    phase = Column(String(24), nullable=False)
    created = Column(Float, nullable=False, default=time.time)
    updated = Column(Float, nullable=False, default=time.time)


def reserve(session, device_id, user_id, attempt_id):
    """Persist a single attempt while holding the same device row as tasks."""
    try:
        if str(uuid.UUID(attempt_id)) != attempt_id:
            raise ValueError()
    except (TypeError, ValueError, AttributeError):
        raise ValueError('invalid_attempt') from None
    device = session.query(Device).filter_by(id=device_id).with_for_update().one_or_none()
    owner = session.query(UserDevice.id).filter_by(device_id=device_id, user_id=user_id).first()
    if device is None or device.is_deleted or not owner:
        raise ValueError('device_not_authorized')
    current = session.query(NativeReload).filter_by(device_id=device_id).with_for_update().one_or_none()
    if current is not None:
        if current.user_id != user_id:
            raise ValueError('attempt_not_authorized')
        if current.attempt_id == attempt_id:
            return current, False
        # A sent, uncertain or accepted reload must only be checked, never
        # replaced by another ID in the hope that the new one succeeds.
        if current.phase not in ('ready', 'not_sent'):
            raise ValueError('reload_unconfirmed')
    if device.state != 'online' or device.controlling:
        raise ValueError('device_unavailable')
    if session.query(AiTask.id).filter(AiTask.device_id == device_id, AiTask.status.notin_(TERMINAL)).first():
        raise ValueError('device_busy')
    if session.query(Task.id).filter(Task.device_id == device_id,
            Task.state.in_(('pending', 'issued', 'running', 'bk_pending', 'bk_active'))).first():
        raise ValueError('device_busy')
    now = time.time()
    if current is None:
        current = NativeReload(device_id=device_id, user_id=user_id, attempt_id=attempt_id,
                               phase='reserved', created=now, updated=now)
        session.add(current)
    else:
        current.attempt_id, current.phase, current.created, current.updated = attempt_id, 'reserved', now, now
    session.flush()
    return current, True


def begin_dispatch(session, device_id, attempt_id):
    """Persist and commit this transition before the single external call."""
    row = session.query(NativeReload).filter_by(device_id=device_id).with_for_update().one()
    if row.attempt_id != attempt_id:
        raise ValueError('attempt_mismatch')
    if row.phase != 'reserved':
        return False
    row.phase, row.updated = 'dispatching', time.time()
    return True


def observe(session, device_id, attempt_id, outcome):
    """Only independent reconnection plus live-guard verification means ready."""
    row = session.query(NativeReload).filter_by(device_id=device_id).with_for_update().one()
    if row.attempt_id != attempt_id:
        raise ValueError('attempt_mismatch')
    allowed = {'accepted': 'awaiting_reconnect', 'unknown': 'unknown',
               'verified': 'ready', 'not_sent': 'not_sent'}
    if outcome not in allowed:
        raise ValueError('invalid_outcome')
    if row.phase in ('ready', 'not_sent') and allowed[outcome] != row.phase:
        raise ValueError('terminal_attempt')
    if outcome == 'not_sent' and row.phase != 'reserved':
        raise ValueError('request_may_have_been_sent')
    if outcome in ('accepted', 'unknown', 'verified') and row.phase == 'reserved':
        raise ValueError('request_not_dispatched')
    row.phase, row.updated = allowed[outcome], time.time()
    return row
