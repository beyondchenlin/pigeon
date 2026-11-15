"""Explicit device/owner pairs enrolled for server-managed native bridges."""
import json
import os
import re
from pathlib import Path


def enrolled():
    path = Path(os.environ.get('BK_BRIDGE_DEVICES_FILE', '/user/phone-pilot/bk-bridge-devices.json'))
    try:
        entries = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(entries, dict) or not all(
        re.fullmatch(r'[a-z0-9]{1,64}', domain) and type(owner) is int and owner > 0
        for domain, owner in entries.items()
    ):
        raise ValueError('invalid_bridge_enrollment')
    return entries
