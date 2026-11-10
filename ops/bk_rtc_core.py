"""Phone-side primitives for BK RTC. No Android imports or network listeners.

The ordered input protocol is deliberately narrower than the existing command
API: only a single touch contact, never shell commands, text, or arbitrary URLs.
"""
import asyncio
import re
import time

VERSION = '1.0.0'
LABEL = 'bk-touch-v1'
PROTOCOL = 'bk.touch.v1'


class TouchProtocol:
    def __init__(self):
        self.sequence = 0
        self.pressed = False
        self.width = self.height = 0

    def configure(self, width, height):
        if not all(type(v) is int and 0 < v <= 16384 for v in (width, height)):
            raise ValueError('Invalid screen dimensions')
        self.width, self.height = width, height

    def validate(self, message):
        sequence = message.get('seq')
        if type(sequence) is not int or sequence != self.sequence + 1:
            raise ValueError('Out of order input')
        action = message.get('action')
        if action not in ('down', 'move', 'up') or not self.width:
            raise ValueError('Unsupported input')
        if (action == 'down') == self.pressed:
            raise ValueError('Invalid gesture transition')
        result = {'action': action, 'id': 0}
        if action != 'up':
            x, y = message.get('x'), message.get('y')
            if not all(type(v) is int for v in (x, y)) or not (0 <= x < self.width and 0 <= y < self.height):
                raise ValueError('Invalid coordinates')
            result.update(x=x, y=y)
        self.sequence = sequence
        self.pressed = action != 'up'
        return result


def is_idr(packet):
    """Annex B access units; never discard arbitrary H.264 dependency frames."""
    return any(nal and nal[0] & 31 == 5 for nal in re.split(b'\x00\x00\x00?\x01', bytes(packet)))


class FreshPacketQueue(asyncio.Queue):
    """Stop feeding old GOPs under pressure; resume at the next random access AU.

    A caller-supplied prepare hook preserves codec parameter sets on IDRs.
    """
    def __init__(self, maxsize=4, max_age=.20, clock=time.monotonic, prepare=None):
        super().__init__(maxsize=maxsize)
        self.clock, self.max_age = clock, max_age
        self.waiting_idr = False
        self.discarded = 0
        self.prepare = prepare

    def _discard(self):
        while not self.empty():
            super().get_nowait()
            self.task_done()
            self.discarded += 1

    def put_nowait(self, packet):
        now = self.clock()
        if packet is None:
            self._discard()
            return super().put_nowait((now, None))
        if self.prepare: packet = self.prepare(packet)
        old = bool(self._queue and now - self._queue[0][0] > self.max_age)
        if self.full() or old:
            self._discard()
            self.waiting_idr = True
        if self.waiting_idr:
            if not is_idr(packet):
                self.discarded += 1
                return
            self.waiting_idr = False
        super().put_nowait((now, packet))

    async def put(self, packet):
        self.put_nowait(packet)

    async def get(self):
        while True:
            try:
                return await super().get()
            except asyncio.QueueEmpty:
                # The only queued GOP expired while the consumer was paused.
                # Wait for the next IDR, rather than ending the media track.
                continue

    def get_nowait(self):
        while True:
            arrival, packet = super().get_nowait()
            self.task_done()
            if packet is None:
                return None
            if self.clock() - arrival > self.max_age:
                self.discarded += 1
                self.waiting_idr = True
                self._discard()
                continue
            return packet
