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
from bk_android_spawn import create_media_process, media_argv

_owner = ContextVar('bk_authenticated_rtc_owner', default=None)
_installed = False
_sessions = weakref.WeakSet()
_readers = weakref.WeakSet()
_events = deque(maxlen=60)
_counters = dict(spawns=0, stops=0, spawn_errors=0, input_sessions=0, input_messages=0, rejected=0)
_media_metrics = dict(access_units=0, bytes=0, last_emit=0, pts_us=None,
                      capture_age_ms=None, max_capture_age_ms=0, sender_frames=0,
                      last_send=0, sender_wait_ms=0)
_forwarding = {'state':'not-configured'}
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
                self.close('task-error')
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
            if m.get('type') == 'release': self.close('client-release'); return
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
        # Tornado can detach the HTTP stream after the WebSocket upgrade.
        # Use the native listener's trusted environment, not the detached stream.
        port = int(os.environ['PORT'])
        if not 1 <= port <= 65535: raise ValueError('Invalid native listener port')
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
            if message is None: self.close('native-touch-closed'); return
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
            # The short lease prevents a stuck finger, not long idle viewing.
            # ICE/peer lifecycle still closes the transport after network loss.
            if time.monotonic() - self.last_seen > 2.5 and (self.protocol.pressed or self.socket is None):
                self.close('lease-expired')

    def close(self, reason='channel-closed'):
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
        record('input-closed', reason=reason)


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
                record('peer-state', state=self.connectionState)
                if self.connectionState in ('closed', 'failed'):
                    for bridge in bridges: bridge.close('peer-'+self.connectionState)
    media.RTCPeerConnection = Peer

    original_close = bundle.SignalingWebSocketHandler.on_close
    def signaling_close(owner):
        for bridge in list(_sessions):
            if bridge.owner is owner: bridge.close('signaling-closed')
        return original_close(owner)
    bundle.SignalingWebSocketHandler.on_close = signaling_close

    # Replace only the media subprocess path; no global Popen monkeypatch.
    original_start, original_stop = media.ScreenProcess.start, media.ScreenProcess.stop
    async def process_start(self, scale=.5, quality=50, fps=30, backend=0, display=0, timeout_sec=8):
        if self.is_running(): return
        native = self._build_command(self.socket_name, scale, quality, fps, backend, display)
        try:
            command = media_argv(native, os.environ['ROOTDIR'])
        except ValueError:
            # Preserve usable native video when this version's command format
            # is not supported. Report shape/executable only, never full argv.
            record('native-command-fallback', kind=type(native).__name__, head=str(native[0])[:100])
            self._bk_native_fallback = True
            return await original_start(self, scale, quality, fps, backend, display, timeout_sec)
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
        if getattr(self, '_bk_native_fallback', False):
            self._bk_native_fallback = False
            return await original_stop(self)
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
    original_emit = media.H264Reader._emit_packet
    def emit_packet(self, payload, pts_us):
        now = time.monotonic()
        _media_metrics.update(access_units=_media_metrics['access_units']+1,
                              bytes=_media_metrics['bytes']+len(payload), last_emit=round(now, 3), pts_us=pts_us)
        # Android MediaCodec uses monotonic microseconds. Reject other clock
        # domains instead of reporting a misleading capture latency.
        age = (now - pts_us/1e6)*1000 if isinstance(pts_us, (int, float)) else -1
        if 0 <= age < 60000:
            _media_metrics['capture_age_ms'] = round(age, 2)
            _media_metrics['max_capture_age_ms'] = round(max(age, _media_metrics['max_capture_age_ms']), 2)
        return original_emit(self, payload, pts_us)
    media.H264Reader._emit_packet = emit_packet
    from aiortc.rtcrtpsender import RTCRtpSender
    original_next = RTCRtpSender._next_encoded_frame
    async def next_frame(self, codec):
        begin = time.monotonic()
        result = await original_next(self, codec)
        if self.kind == 'video' and result is not None:
            _media_metrics.update(sender_frames=_media_metrics['sender_frames']+1,
                                  last_send=round(time.monotonic(), 3),
                                  sender_wait_ms=round((time.monotonic()-begin)*1000, 2))
        return result
    RTCRtpSender._next_encoded_frame = next_frame
    # Reapply after the native generator has written service configs. Only the
    # owned fwd program is reconfigured; no recurring restarts or binary edits.
    from tornado.ioloop import IOLoop
    async def forwarding():
        try:
            from bk_forward_runtime import configure
            from lamda.globals import supervisor
            _forwarding['state']=await asyncio.to_thread(configure,supervisor,os.environ['USRDIR'])
            record('forwarding-configured')
        except FileNotFoundError:_forwarding['state']='not-installed'
        except Exception as error:
            _forwarding['state']='configuration-failed';record('forwarding-error',kind=type(error).__name__)
    IOLoop.current().call_later(3,lambda:asyncio.create_task(forwarding()))
    _installed = True
    record('installed', version=VERSION, pid=os.getpid(), uid=os.getuid())


class BkRtcExtension(BaseHttpExtension):
    route = '/bk-rtc/health'
    name = 'BK RTC transport'
    version = VERSION

    @staticmethod
    def setup(config):
        install()

    def http_get(self):
        self.write_json(dict(version=VERSION, installed=_installed, uid=os.getuid(), pid=os.getpid(),
            counters=_counters, media=_media_metrics, forwarding=_forwarding, input_sessions=len(_sessions), readers=[dict(depth=r._packet_queue.qsize(),
            discarded=r._packet_queue.discarded) for r in _readers], events=list(_events)))
