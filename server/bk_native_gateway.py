"""Durable admission records for proxied device connections.

The gateway must register before forwarding and unregister only after the
connection has ended. A missing close notification leaves a conservative stale
record; it must never be expired on time alone.
"""
import time
import uuid
from pathlib import Path

from sqlalchemy import Column, Float, ForeignKey, Integer, String
from executor.models import Base, Device


class NativeGatewayConnection(Base):
    __tablename__ = 'bk_native_gateway_connection'
    connection_id = Column(String(36), primary_key=True)
    device_id = Column(Integer, ForeignKey('device.id'), nullable=False, index=True)
    worker_pid = Column(Integer, nullable=False)
    worker_start = Column(String(32), nullable=False)
    created = Column(Float, nullable=False, default=time.time)


def canonical_id(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid_connection_id') from None
    return value


def register(session, domain, connection_id, worker_pid, worker_start):
    """Atomically register or reject a connection against reload admission."""
    canonical_id(connection_id)
    if type(worker_pid) is not int or worker_pid <= 0 or not isinstance(worker_start, str) or not worker_start.isdecimal() or len(worker_start) > 32:
        raise ValueError('invalid_worker')
    device = session.query(Device).filter_by(domain=domain).with_for_update().one_or_none()
    if device is None or device.is_deleted:
        raise ValueError('device_unavailable')
    existing = session.get(NativeGatewayConnection, connection_id)
    if existing:
        if (existing.device_id != device.id or existing.worker_pid != worker_pid
                or existing.worker_start != worker_start):
            raise ValueError('connection_id_conflict')
        return existing, False
    from .bk_native_reload import NativeReload
    pending = session.query(NativeReload.device_id).filter(
        NativeReload.device_id == device.id,
        NativeReload.phase.notin_(('ready', 'not_sent'))).first()
    if pending:
        raise ValueError('reload_in_progress')
    record = NativeGatewayConnection(connection_id=connection_id, device_id=device.id,
                                     worker_pid=worker_pid, worker_start=worker_start)
    session.add(record)
    session.flush()
    return record, True


def unregister(session, domain, connection_id):
    """Forget only the exact connection after its request actually ends."""
    canonical_id(connection_id)
    device = session.query(Device).filter_by(domain=domain).with_for_update().one_or_none()
    if device is None:
        return False
    record = session.get(NativeGatewayConnection, connection_id)
    if record is None:
        return False
    if record.device_id != device.id:
        raise ValueError('connection_id_conflict')
    session.delete(record)
    session.flush()
    return True


def worker_gone(pid, expected_start):
    """Only a missing process or a different start time proves it ended."""
    try:
        data = (Path('/proc') / str(pid) / 'stat').read_text()
        return data.rsplit(')', 1)[1].split()[19] != expected_start
    except FileNotFoundError:
        return True
    except (OSError, ValueError, IndexError, UnicodeError):
        return False


def remove_dead_worker_connections(session, device_id):
    """Caller holds the device row lock and has verified the gateway."""
    records = session.query(NativeGatewayConnection).filter_by(device_id=device_id).all()
    removed = 0
    for record in records:
        if worker_gone(record.worker_pid, record.worker_start):
            session.delete(record)
            removed += 1
    session.flush()
    return removed
