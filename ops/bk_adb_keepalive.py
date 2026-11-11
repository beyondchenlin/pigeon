"""Maintain only the existing, private single-phone ADB transport.

No pairing, debug settings, phone commands, TCP listeners or ADB-server restart.
The old STF bridge is deliberately left stopped.
"""
import argparse
import logging
from logging.handlers import RotatingFileHandler
import socket
import time

SERIAL = '127.0.0.1:15555'


class AdbProtocolError(Exception):
    pass


def request(path, command):
    def exact(client, count):
        data = bytearray()
        while len(data) < count:
            chunk = client.recv(count-len(data))
            if not chunk:
                raise AdbProtocolError('Truncated ADB response')
            data.extend(chunk)
        return bytes(data)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(8)
        client.connect(path)
        encoded = command.encode('ascii')
        client.sendall(('%04x' % len(encoded)).encode('ascii') + encoded)
        status = exact(client, 4)
        if status != b'OKAY':
            # Do not log raw server responses, device lists or credentials.
            raise AdbProtocolError('ADB command not accepted')
        try:
            size = int(exact(client, 4), 16)
        except ValueError as error:
            raise AdbProtocolError('Invalid ADB response length') from error
        return exact(client, size).decode('utf-8', errors='replace')


def state_of(devices):
    for line in devices.splitlines():
        parts = line.split('\t', 1)
        if len(parts) == 2 and parts[0] == SERIAL:
            return parts[1].strip()
    return 'missing'


def maintain(adb):
    state = state_of(adb('host:devices'))
    if state == 'device':
        return state
    if state == 'offline':
        # A plain connect can reply "already connected" while the old TLS
        # transport stays offline. Disconnect only this reserved endpoint.
        adb('host:disconnect:' + SERIAL)
    elif state != 'missing':
        return state  # Unauthorized/other states never trigger pairing/reset.
    adb('host:connect:' + SERIAL)
    # A success-shaped string is not an online device. Verify actual state.
    return state_of(adb('host:devices'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--log', required=True)
    args = parser.parse_args()
    logger = logging.getLogger('bk.adb-maintenance')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(args.log, maxBytes=1024*1024, backupCount=2)
    logger.addHandler(handler)
    previous = None
    while True:
        try:
            state = maintain(lambda command: request(args.socket, command))
            if state not in ('device', 'offline', 'missing', 'unauthorized'):
                state = 'unavailable'
        except (OSError, AdbProtocolError):
            state = 'unavailable'
        if state != previous:
            logger.info('%d %s', time.time(), state)
            previous = state
        time.sleep(15)


if __name__ == '__main__':
    main()
