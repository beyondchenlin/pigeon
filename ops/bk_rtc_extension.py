"""Version-gated FIRERPA web extension: authenticated P2P touch + media lifecycle.

Install under USRDIR/modules/extension; dependencies live in USRDIR/modules.
No public listener, no root, and no replacement of the native binary/modules.
"""
import asyncio
from collections import deque
from contextvars import ContextVar
import json
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
import re
from pathlib import Path
import ssl
import time
import weakref

from lamda.extensions import BaseHttpExtension
from bk_rtc_core import LABEL, PROTOCOL, VERSION, FreshPacketQueue, TouchProtocol
from bk_android_spawn import create_media_process

_owner = ContextVar('bk_authenticated_rtc_owner', default=None)
_installed = False
_sessions = weakref.WeakSet()
_readers = weakref.WeakSet()
_events = deque(maxlen=60)
_counters = dict(spawns=0, stops=0, spawn_errors=0, input_sessions=0, input_messages=0, rejected=0)
log = logging.getLogger('bk.rtc')


def record(event, **fields):
    entry = dict(time=round(time.time(), 3), event=event, **fields)
    _events.append(entry)
    log.info(json.dumps(entry, separators=(',', ':')))


class TouchBridge:
    def __init__(self, owner, channel):
        self.owner, self.channel = owner, channel
        self.closed = False
        self.socket = None
        self.protocol = TouchProtocol()
        self.queue = asyncio.Queue(maxsize=24)
        self.last_seen = time.monotonic()
        self.tasks = set()
        self.claiming = False
        _sessions.add(self)
        channel.on('message', self.message)
        channel.on('close', self.close)
        if channel.readyState == 'open': self.send({'type': 'capability', 'version': 1})
        else: channel.on('open', lambda: self.send({'type': 'capability', 'version': 1}))
        self.task(self.watchdog())

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def done(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception():
                record('input-task-error', kind=type(task.exception()).__name__)
                self.close()
        task.add_done_callback(done)
        return task

    def send(self, message):
        if not self.closed and self.channel.readyState == 'open':
            if self.channel.bufferedAmount > 16384: self.close(); return
            try: self.channel.send(json.dumps(message, separators=(',', ':')))
            except Exception: self.close()

    def message(self, raw):
        if self.closed: return
        try:
            if not isinstance(raw, str) or len(raw) > 1024: raise ValueError('Oversized message')
            m = json.loads(raw)
            if not isinstance(m, dict): raise ValueError('Invalid message')
            self.last_seen = time.monotonic()
            if m.get('type') == 'ping':
                self.send({'type': 'pong', 'id': m.get('id')}); return
            if m.get('type') == 'claim' and not self.claiming:
                self.claiming = True; self.task(self.connect()); return
            if m.get('type') == 'release': self.close(); return
            if m.get('type') != 'touch' or self.socket is None: raise ValueError('Not ready')
            payload = self.protocol.validate(m)
            self.queue.put_nowait((time.monotonic(), m['seq'], payload))
        except (ValueError, TypeError, KeyError, asyncio.QueueFull):
            _counters['rejected'] += 1
            self.close()

    async def connect(self):
        from tornado.httpclient import HTTPRequest
        from tornado.websocket import websocket_connect
        # The existing authenticated signaling handler owns this bridge. Copy
        # only its device token/client identity, never browser-controlled URLs.
        request = self.owner.request
        token = request.headers.get('X-Token')
        if not token: raise ValueError('Cloud-authenticated device session required')
        port = request.connection.stream.socket.getsockname()[1]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
        headers = {k: request.headers[k] for k in ('X-Token', 'X-ClientId', 'Host', 'Origin') if k in request.headers}
        # TLS verification is disabled only on fixed loopback back into this
        # same native service, matching its local self-signed device endpoint.
        ws = await websocket_connect(HTTPRequest(f'wss://127.0.0.1:{port}/ws/screen/touch', headers=headers,
            ssl_options=context, connect_timeout=3, request_timeout=3), max_message_size=4096)
        if self.closed: ws.close(); return
        self.socket = ws
        await ws.write_message(json.dumps({'action': 'reset'}))
        while not self.closed:
            raw = await asyncio.wait_for(ws.read_message(), 3)
            if raw is None: raise ConnectionError('Native touch closed')
            m = json.loads(raw)
            if m.get('type') == 'OK':
                self.protocol.configure(m['w'], m['h'])
                self.send({'type': 'ready', 'w': m['w'], 'h': m['h']})
                _counters['input_sessions'] += 1
                self.task(self.writer()); self.task(self.reader())
                record('input-ready')
                return

    async def reader(self):
        while not self.closed:
            message = await self.socket.read_message()
            if message is None: self.close(); return
            # Conflicting native controller / changed display must renegotiate.
            try:
                m = json.loads(message)
                if m.get('type') == 'OK' and (m.get('w'), m.get('h')) != (self.protocol.width, self.protocol.height):
                    self.close(); return
            except (ValueError, TypeError): pass

    async def writer(self):
        while not self.closed:
            arrival, seq, payload = await self.queue.get()
            if time.monotonic() - arrival > .35:
                self.close(); return
            await asyncio.wait_for(self.socket.write_message(json.dumps(payload)), .35)
            _counters['input_messages'] += 1
            self.send({'type': 'ack', 'seq': seq})

    async def watchdog(self):
        while not self.closed:
            await asyncio.sleep(.5)
            if time.monotonic() - self.last_seen > 2.5:
                self.close()

    def close(self):
        if self.closed: return
        self.closed = True
        for task in list(self.tasks):
            if task is not asyncio.current_task(): task.cancel()
        if self.socket:
            # Best effort release followed by native on_close cleanup. Never
            # replay the in-flight gesture on WSS or after reconnection.
            try: self.socket.write_message(json.dumps({'action': 'up', 'id': 0}))
            except Exception: pass
            self.socket.close(); self.socket = None
        self.channel.close()
        _sessions.discard(self)
        record('input-closed')


def install():
    global _installed
    if _installed: return
    import lamda
    import lamda.web.bundle as bundle
    import lamda.webrtc as media
    if str(lamda.__version__) != '10.9': raise RuntimeError('Unverified FIRERPA runtime')
    pinned = {media.__file__: '4d4fc99598b3ef034151b65e45227a3990ff3c68735228303ab6fea71f750fe6',
              bundle.__file__: '03841b518f9a1308de3af62ae2fddbf4b5c0b918fa4e4aa701c5771a720fd05f'}
    for file, digest in pinned.items():
        if hashlib.sha256(Path(file).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Native media changed; extension requires revalidation')
    path = Path(os.environ['USRDIR']) / 'phone-pilot/bk-rtc.log'
    path.parent.mkdir(mode=0o700, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2*1024*1024, backupCount=2)
    log.addHandler(handler); log.setLevel(logging.INFO); log.propagate = False

    original_offer = bundle.SignalingWebSocketHandler.cb_request_offer
    async def offer(owner, data):
        marker = _owner.set(owner)
        try: return await original_offer(owner, data)
        finally: _owner.reset(marker)
    bundle.SignalingWebSocketHandler.cb_request_offer = offer

    original_peer = media.RTCPeerConnection
    class Peer(original_peer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            owner = _owner.get()
            bridges = []
            @self.on('datachannel')
            def channel(channel):
                if owner is None or channel.label != LABEL or channel.protocol != PROTOCOL or not channel.ordered or bridges:
                    channel.close(); return
                bridges.append(TouchBridge(owner, channel))
            @self.on('connectionstatechange')
            def state():
                if self.connectionState in ('closed', 'failed'):
                    for bridge in bridges: bridge.close()
    media.RTCPeerConnection = Peer

    original_close = bundle.SignalingWebSocketHandler.on_close
    def signaling_close(owner):
        for bridge in list(_sessions):
            if bridge.owner is owner: bridge.close()
        return original_close(owner)
    bundle.SignalingWebSocketHandler.on_close = signaling_close

    # Replace only the media subprocess path; no global Popen monkeypatch.
    async def process_start(self, scale=.5, quality=50, fps=30, backend=0, display=0, timeout_sec=8):
        if self.is_running(): return
        command = self._build_command(self.socket_name, scale, quality, fps, backend, display)
        t = time.monotonic(); record('spawn-begin')
        try:
            self.proc = await create_media_process(command)
            _counters['spawns'] += 1
            record('spawned', pid=self.proc.pid, ms=round((time.monotonic()-t)*1000))
            ready = await asyncio.wait_for(self._wait_for_ok(self.proc.stderr, timeout_sec), timeout_sec)
            if ready is False: raise TimeoutError('Native encoder did not become ready')
            self._stderr_task = asyncio.create_task(self._drain_stderr())
        except BaseException:
            _counters['spawn_errors'] += 1
            await process_stop(self)
            raise
    async def process_stop(self):
        task, self._stderr_task = self._stderr_task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        process, self.proc = self.proc, None
        if process:
            process.terminate()
            try: await asyncio.wait_for(process.wait(), 1)
            except asyncio.TimeoutError:
                process.kill(); await asyncio.wait_for(process.wait(), 1)
            finally: process.close_pipes()
            _counters['stops'] += 1
            record('media-stopped', pid=process.pid)
    media.ScreenProcess.start = process_start
    media.ScreenProcess.stop = process_stop

    original_reader = media.H264Reader.__init__
    def reader_init(self, *args, **kwargs):
        original_reader(self, *args, **kwargs)
        headers = {}
        def prepare(packet):
            nals = [n for n in re.split(b'\x00\x00\x00?\x01', bytes(packet)) if n]
            types = {n[0] & 31 for n in nals}
            for nal in nals:
                if nal[0] & 31 in (7, 8): headers[nal[0] & 31] = nal
            missing = [headers[t] for t in (7, 8) if t not in types and t in headers]
            if 5 in types and missing:
                # Recreate the encoded packet, preserving its media timestamps.
                # The native reader does not always repeat SPS/PPS on each IDR.
                repaired = type(packet)(b''.join(b'\x00\x00\x00\x01'+n for n in missing)+bytes(packet))
                repaired.pts, repaired.dts = packet.pts, packet.dts
                if packet.time_base is not None: repaired.time_base = packet.time_base
                return repaired
            return packet
        self._packet_queue = FreshPacketQueue(prepare=prepare)
        _readers.add(self)
    media.H264Reader.__init__ = reader_init
    _installed = True
    record('installed', version=VERSION, pid=os.getpid(), uid=os.getuid())


class BkRtcExtension(BaseHttpExtension):
    route = '/bk-rtc/health'
    name = 'BK RTC transport'
    version = VERSION

    @staticmethod
    def setup(config):
        install()

    async def http_get(self):
        self.write_json(dict(version=VERSION, installed=_installed, uid=os.getuid(), pid=os.getpid(),
            counters=_counters, input_sessions=len(_sessions), readers=[dict(depth=r._packet_queue.qsize(),
            discarded=r._packet_queue.discarded) for r in _readers], events=list(_events)))
