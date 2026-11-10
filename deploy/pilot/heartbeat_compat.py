"""FIRERPA 10.9 liveness compatibility for the pinned PIGEON 1.0 pilot.

The phone emits task/status periodically; this image expires devices without
refreshing their heartbeat from that event. Accept only fresh, non-retained,
topic-matching events for the enrolled pilot. Original task handling is retained.
"""
import logging
import math
import runpy
import socket
import time

DOMAIN = 'umdax0nurk9o'
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


def install():
    from executor.event import EventConnectionService
    from executor.models import Device, session_scope
    from executor.handlers.event import set_gateway

    original = EventConnectionService.mq_on_message
    logger = logging.getLogger('phone-pilot-heartbeat')
    last_gateway_probe = {}

    def on_message(self, client, userdata, packet):
        properties = dict(packet.properties.UserProperty or [])
        message = self.mq_decode(packet.payload, encoding=properties.get('encode'))
        now = time.time()
        identity = eligible_identity(packet.topic, packet.retain, message, now)
        answer = original(self, client, userdata, packet)
        if identity:
            try:
                with session_scope() as session:
                    device = session.query(Device).filter(
                        Device.domain == DOMAIN,
                        Device.dev_id == identity,
                        Device.is_deleted.is_(False),
                    ).one_or_none()
                    if device is not None and now - (device.last_heartbeat_time or 0) >= 8:
                        was_offline = device.state == 'offline'
                        device.last_heartbeat_time = now
                        if was_offline:
                            device.state = 'online'
                            logger.info('Pilot online from fresh authenticated phone event')
                        if now - last_gateway_probe.get(identity, 0) >= 30:
                            last_gateway_probe[identity] = now
                            refresh_forward_gateway(device, socket.create_connection, set_gateway)
                        session.flush()
            except Exception as exc:
                logger.error('Heartbeat compatibility update failed: %s', type(exc).__name__)
        return answer

    # Consume its sequence through the original event queue, without inventing
    # the many device/status fields absent from the newer short status payload.
    if not hasattr(EventConnectionService, 'handle_STATUS'):
        def handle_status(self, message):
            return self.dispatch(message['device_id'], 'PING', message)
        EventConnectionService.handle_STATUS = handle_status
    EventConnectionService.mq_on_message = on_message
    logger.warning('Pilot heartbeat compatibility enabled for fresh phone status events')


if __name__ == '__main__':
    install()
    runpy.run_module('executor', run_name='__main__')
