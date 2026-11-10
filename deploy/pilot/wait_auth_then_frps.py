"""Wait for PIGEON's local FRP authenticator before accepting phone logins."""
import os
import socket
import sys
import time


def wait_for_auth(timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', 8123), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError('Local PIGEON authenticator did not start within 60 seconds')


if __name__ == '__main__':
    wait_for_auth()
    print('PIGEON local authenticator is listening', flush=True)
    if sys.argv[1:] != ['--check-only']:
        os.environ['BK_FRPS_BIN'] = '/user/phone-pilot/frps-0.72.0-linux-amd64'
        os.execv('/user/phone-pilot/bk-frps.sh', ['/user/phone-pilot/bk-frps.sh', '-c', '/etc/frps.toml'])
