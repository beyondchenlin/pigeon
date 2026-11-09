"""Short-lived TURN credentials, issued only after device ownership validation."""
import base64
import hashlib
import hmac
import json
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
        if not CONFIG.is_file():
            self.tell({'iceServers': [{'urls': 'stun:stun.device-farm.com'}]})
            return
        self.tell(issue(json.loads(CONFIG.read_text()), domain))
