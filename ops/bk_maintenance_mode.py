"""Explicit opt-in for the added maintenance processes; missing means off."""
import json
import os
from pathlib import Path
import uuid


def read_mode(user):
    try:
        value = json.loads((Path(user) / 'phone-pilot/lifecycle/maintenance.json').read_text())
        if type(value.get('enabled')) is not bool or str(uuid.UUID(value['request_id'])) != value['request_id']:
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {'enabled': False, 'request_id': None}


def enabled(user):
    return read_mode(user)['enabled']


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('Unexpected state link')
    temp = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    try:
        with temp.open('x') as stream:
            os.chmod(temp, 0o600)
            json.dump(value, stream)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
