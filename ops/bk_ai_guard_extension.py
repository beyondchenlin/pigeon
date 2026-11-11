"""Stop BK before authenticated native touch/keyboard/debug input is delivered."""
import asyncio
import hashlib
import inspect
import os
from pathlib import Path
from lamda.extensions import BaseHttpExtension
from bk_ai_phone import activate_guard, enter_manual, leave_manual

INSTALLED = False


def connection_closed(handler):
    connection = getattr(handler, 'ws_connection', None)
    return (getattr(handler, '_bk_ai_closed', False) or connection is None
            or connection.stream.closed())


def install():
    global INSTALLED
    if INSTALLED:
        return
    import lamda
    import lamda.web.bundle as bundle
    if str(lamda.__version__) != '10.9' or hashlib.sha256(Path(bundle.__file__).read_bytes()).hexdigest() != '03841b518f9a1308de3af62ae2fddbf4b5c0b918fa4e4aa701c5771a720fd05f':
        raise RuntimeError('Unverified native input runtime')
    root = str(Path(os.environ['USRDIR']) / 'phone-pilot/bk-ai')
    for name in ('TouchInputHandler', 'RealTimeCmdHandler', 'CodeDebugeeHandler', 'DebugWebSocketHandler'):
        cls = getattr(bundle, name)
        original_message, original_close = cls.on_message, cls.on_close

        async def message(self, raw, original=original_message, kind=name):
            # Monitoring/clipboard reads must not seize control merely by viewing.
            if kind == 'RealTimeCmdHandler':
                import json
                try:
                    value = {} if raw == 'status' else json.loads(raw)
                    readonly = raw == 'status' or value.get('command') in ('device/info', 'display/list', 'clipboard/get')
                except (ValueError, TypeError, AttributeError):
                    readonly = False
                if readonly:
                    result = original(self, raw)
                    return await result if inspect.isawaitable(result) else result
            if not hasattr(self, '_bk_ai_lock'):
                self._bk_ai_lock = asyncio.Lock()
            async with self._bk_ai_lock:
                if connection_closed(self):
                    return
                if not getattr(self, '_bk_ai_claim', None):
                    try:
                        fd = self.ws_connection.stream.socket.fileno()
                        claim = await asyncio.to_thread(enter_manual, root, socket_fd=fd)
                    except Exception:
                        self.close(code=1013, reason='AI stop unconfirmed; input blocked')
                        return
                    if connection_closed(self):
                        await asyncio.to_thread(leave_manual, root, claim)
                        return
                    self._bk_ai_claim = claim
            result = original(self, raw)
            return await result if inspect.isawaitable(result) else result

        def close(self, original=original_close):
            self._bk_ai_closed = True
            claim = getattr(self, '_bk_ai_claim', None)
            if claim:
                self._bk_ai_claim = None
                leave_manual(root, claim)
            return original(self)

        cls.on_message, cls.on_close = message, close
    activate_guard(root)
    INSTALLED = True


class BkAiGuardExtension(BaseHttpExtension):
    route = '/bk-ai/health'
    name = 'BK AI input arbitration'
    version = '1.0.0'

    @staticmethod
    def setup(config):
        install()

    def http_get(self):
        self.write_json({'installed': INSTALLED, 'pid': os.getpid(), 'version': self.version})
