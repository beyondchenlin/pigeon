"""Run inside the existing PIGEON image; never prints a usable credential."""
import base64
import hashlib
import hmac
import sys
from unittest.mock import patch
import server
server.__path__.append('/user/phone-pilot/bk-server')
from server import bk_ice as i

config = {'secret': 'unit-test-secret', 'stun_urls': ['stun:example.test:3478'],
          'turn_urls': ['turn:example.test:3478?transport=udp']}
issued = i.issue(config, 'testdevice', 1000)
turn = issued['iceServers'][1]
assert issued['expiresAt'] == 4600
assert turn['username'].startswith('4600:testdevice:')
assert turn['credential'] == base64.b64encode(hmac.new(config['secret'].encode(), turn['username'].encode(), hashlib.sha1).digest()).decode()
assert config['secret'] not in str(issued)
assert i.issue(config, 'testdevice', 1000)['iceServers'][1]['username'] != turn['username']
class Unauthorized:
    def get_login_user_device(self, domain):
        raise PermissionError('not device owner')
with patch.object(i, 'issue') as issue:
    try:
        i.IceHandler.get(Unauthorized(), 'testdevice')
    except PermissionError:
        pass
    else:
        raise AssertionError('missing ownership validation')
    issue.assert_not_called()
print('PASS: expiring TURN HMAC, per-request nonce, no shared secret exposure, ownership before issue')
