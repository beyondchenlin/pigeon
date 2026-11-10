import asyncio
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from bk_rtc_core import TouchProtocol, FreshPacketQueue, is_idr

IDR = b'\x00\x00\x00\x01\x67sps\x00\x00\x01\x68pps\x00\x00\x01\x65frame'
P = b'\x00\x00\x01\x41delta'


class ProtocolTests(unittest.TestCase):
    def test_gesture_and_replay(self):
        p = TouchProtocol(); p.configure(1080, 2388)
        down = dict(seq=1, action='down', x=20, y=30)
        self.assertEqual(p.validate(down)['action'], 'down')
        with self.assertRaises(ValueError): p.validate(down)
        p.validate(dict(seq=2, action='move', x=50, y=60))
        p.validate(dict(seq=3, action='up'))
        self.assertFalse(p.pressed)
        with self.assertRaises(ValueError): p.validate(dict(seq=4, action='move', x=5, y=5))

    def test_untrusted_input_does_not_advance_sequence(self):
        for invalid in [dict(seq=True, action='down', x=1, y=1), dict(seq=1, action='shell'),
                        dict(seq=1, action='down', x=1080, y=1), dict(seq=1, action='down', x=float('nan'), y=1)]:
            p = TouchProtocol(); p.configure(1080, 2388)
            with self.assertRaises(ValueError): p.validate(invalid)
            self.assertEqual(p.sequence, 0)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_overload_waits_for_idr_not_corrupt_p_frames(self):
        q = FreshPacketQueue(maxsize=2)
        await q.put(IDR); await q.put(P); await q.put(P)
        self.assertTrue(q.empty()); self.assertTrue(q.waiting_idr)
        await q.put(P); self.assertTrue(q.empty())
        await q.put(IDR); await q.put(P)
        self.assertEqual(await q.get(), IDR); self.assertEqual(await q.get(), P)
        await q.join()

    async def test_stale_queue_does_not_end_track(self):
        now = [0.0]; q = FreshPacketQueue(clock=lambda: now[0])
        await q.put(IDR); now[0] = .5
        waiter = asyncio.create_task(q.get()); await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        await q.put(P); await q.put(IDR)
        self.assertEqual(await asyncio.wait_for(waiter, .1), IDR)

    async def test_stop_wakes_waiter_even_at_capacity(self):
        q = FreshPacketQueue(maxsize=1); await q.put(IDR); await q.put(None)
        self.assertIsNone(await q.get()); await q.join()
        self.assertTrue(is_idr(IDR)); self.assertFalse(is_idr(P))

if __name__ == '__main__': unittest.main()
