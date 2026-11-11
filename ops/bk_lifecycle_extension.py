"""Start the narrowly scoped, independent native startup monitor."""
import hashlib
import logging
import os
from pathlib import Path
from lamda.extensions import BaseHttpExtension
from bk_lifecycle_monitor import NATIVE_HASH, start_monitor


class BkLifecycleExtension(BaseHttpExtension):
    route = '/bk-lifecycle/health'
    name = 'BK native startup recovery'
    version = '1.0.0'

    @staticmethod
    def setup(config):
        import lamda
        # The optional recovery layer must never turn an unsupported version
        # or a failed observer launch into another primary-service startup error.
        try:
            native = Path(os.environ['PKGDIR']) / 'web/bundle.cpython-312.so'
            if str(lamda.__version__) != '10.9' or hashlib.sha256(native.read_bytes()).hexdigest() != NATIVE_HASH:
                logging.getLogger(__name__).warning('Lifecycle recovery disabled: unverified runtime')
                return
            start_monitor(os.environ['ROOTDIR'], os.environ['USRDIR'])
        except (OSError, ValueError, KeyError, RuntimeError):
            logging.getLogger(__name__).warning('Lifecycle recovery unavailable; primary service continues')

    def http_get(self):
        import json
        path = Path(os.environ['USRDIR']) / 'phone-pilot/lifecycle/status.json'
        self.write_json(json.loads(path.read_text()) if path.exists() else {'state':'not_ready'})
