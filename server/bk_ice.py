"""Short-lived TURN credentials, issued only after device ownership validation."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from server.service import BaseHttpService

CONFIG = Path('/user/phone-pilot/bk-turn.json')


def issue(config, domain, now=None):
    expires = int(time.time() if now is None else now) + 3600
    username = f'{expires}:{domain}:{secrets.token_hex(6)}'
    credential = base64.b64encode(hmac.new(config['secret'].encode(), username.encode(), hashlib.sha1).digest()).decode()
    return {'iceServers': [{'urls': config['stun_urls']},
                           {'urls': config['turn_urls'], 'username': username, 'credential': credential}],
            'expiresAt': expires}


class IceHandler(BaseHttpService):
    def get(self, domain):
        self.get_login_user_device(domain)
        self.set_header('Cache-Control', 'no-store')
        host = os.environ.get('BK_TURN_HOST')
        if host:
            config = {'secret': os.environ['BK_TURN_SECRET'], 'stun_urls': [f'stun:{host}:3478'],
                      'turn_urls': [f'turn:{host}:3478?transport=udp', f'turn:{host}:3478?transport=tcp']}
            if not config['secret']:
                self.throw(503, 'TURN_CONFIG', 'TURN secret is missing')
            self.tell(issue(config, domain))
        elif not CONFIG.is_file():
            self.tell({'iceServers': [{'urls': 'stun:stun.device-farm.com'}]})
        else:
            self.tell(issue(json.loads(CONFIG.read_text()), domain))
