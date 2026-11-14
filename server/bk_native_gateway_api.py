"""Loopback-only gateway admission, authenticated by a host-private secret."""
import hmac
import json
import os
from pathlib import Path
import re
import stat

from tornado.web import RequestHandler
from executor.models import session_scope
from .bk_native_gateway import register, unregister


def worker_matches(pid, start):
    if type(pid) is not int or pid <= 0 or not isinstance(start, str) or not start.isdecimal():
        return False
    try:
        root = Path('/proc') / str(pid)
        command = (root / 'cmdline').read_bytes().replace(b'\0', b' ')
        identity = (root / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        return command.startswith(b'nginx: worker process') and identity == start
    except (OSError, ValueError, IndexError):
        return False


def load_secret():
    path = Path(os.environ.get('BK_GATEWAY_SECRET_FILE', '/user/phone-pilot/bk-gateway-secret'))
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('unsafe_secret_file')
    value = path.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('invalid_secret')
    return value


class GatewayAdmissionHandler(RequestHandler):
    def prepare(self):
        self.set_header('Cache-Control', 'no-store')
        if self.request.remote_ip not in ('127.0.0.1', '::1'):
            self.send_error(403)
            return
        try:
            supplied = self.request.headers.get('X-BK-Gateway-Key', '')
            if not hmac.compare_digest(load_secret(), supplied):
                self.send_error(403)
                return
        except (OSError, ValueError, UnicodeError, TypeError):
            self.send_error(503)

    def post(self):
        if len(self.request.body) > 4096:
            self.send_error(413)
            return
        try:
            body = json.loads(self.request.body)
            if (not isinstance(body, dict) or body.get('action') not in ('open', 'close')
                    or set(body) != ({'action', 'domain', 'connection_id', 'worker_pid', 'worker_start'}
                                     if body.get('action') == 'open' else {'action', 'domain', 'connection_id'})
                    or body['action'] not in ('open', 'close')
                    or not isinstance(body['domain'], str)
                    or not re.fullmatch(r'[a-z0-9]{1,64}', body['domain'])):
                raise ValueError('invalid_request')
            with session_scope() as session:
                if body['action'] == 'open':
                    if not worker_matches(body['worker_pid'], body['worker_start']):
                        raise ValueError('invalid_worker')
                    register(session, body['domain'], body['connection_id'], body['worker_pid'], body['worker_start'])
                else:
                    unregister(session, body['domain'], body['connection_id'])
        except (ValueError, TypeError, KeyError, UnicodeError):
            self.send_error(409)
            return
        self.set_status(204)
        self.finish()
