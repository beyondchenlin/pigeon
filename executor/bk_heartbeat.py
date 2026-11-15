"""FIRERPA 10.9 heartbeat support for enrolled devices on a standalone host."""
import logging
import math
import socket

LIVENESS_EVENTS = frozenset({'task/status', 'status', 'ping'})


def eligible_identity(topic, retained, message, now):
    if retained or not isinstance(message, dict):
        return None
    identity = message.get('device_id')
    if not isinstance(identity, str) or not identity or '/' in identity:
        return None
    if topic != f'device/{identity}/event' or message.get('type') not in LIVENESS_EVENTS:
        return None
    if not isinstance(message.get('data'), dict):
        return None
    timestamp = message.get('timestamp')
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    seconds = timestamp / 1000.0  # confirmed millisecond timestamp in phone 10.9 packets
    if not math.isfinite(seconds) or not -30 <= now - seconds <= 90:
        return None
    return identity


def refresh_forward_gateway(device, connect, publish):
    """Renew the original authenticated route only while its FRP port is live."""
    port = device.service_port
    if device.mode != 'forward' or device.state != 'online':
        return False
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        return False
    try:
        with connect(('127.0.0.1', port), timeout=0.3):
            pass
    except OSError:
        return False
    publish(device, '127.0.0.1', port)
    return True

def record(topic, retained, message, now, last_probe):
    from .models import Device, session_scope
    from .handlers.event import set_gateway

    identity = eligible_identity(topic, retained, message, now)
    if not identity:
        return
    try:
        with session_scope() as session:
            device = session.query(Device).filter(
                Device.dev_id == identity, Device.is_deleted.is_(False),
                Device.state.in_(('online', 'offline')),
            ).one_or_none()
            if device is None or now - (device.last_heartbeat_time or 0) < 8:
                return
            device.last_heartbeat_time = now
            device.state = 'online'
            if now - last_probe.get(identity, 0) >= 30:
                last_probe[identity] = now
                refresh_forward_gateway(device, socket.create_connection, set_gateway)
    except Exception as error:
        logging.getLogger(__name__).error('Heartbeat update failed: %s', type(error).__name__)
