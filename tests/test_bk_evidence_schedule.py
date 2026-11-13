"""Exercise the real scheduler with blocked workers and persisted-terminal signals.

Database and HTTP classes are import seams only; no product service or phone is
started. The real async scheduler and executor run, including wakeup races.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

models = ModuleType('executor.models')
models.Device = SimpleNamespace(domain='domain', id='id', state='state')
models.session_scope = MagicMock()
service = ModuleType('server.service')
service.BaseHttpService = type('BaseHttpService', (), {})
task_store = ModuleType('server.bk_ai_store')
task_store.AiTask = SimpleNamespace(device_id='device_id')
spec = importlib.util.spec_from_file_location('server._bk_evidence_schedule_test',
    Path(__file__).resolve().parents[1] / 'server/bk_evidence.py')
evidence = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'executor.models': models, 'server.service': service,
                            'server.bk_ai_store': task_store}):
    spec.loader.exec_module(evidence)


class EvidenceScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with evidence.SCHEDULE_LOCK:
            evidence.INFLIGHT.clear()
            evidence.NEXT.clear()
            evidence.HEALTH.clear()
            evidence.WAKEUPS.clear()
            evidence.SEEN_TERMINALS.clear()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.patch_pool = patch.object(evidence, 'POOL', self.pool)
        self.patch_pool.start()
        self.addCleanup(self.patch_pool.stop)
        self.addCleanup(self.pool.shutdown, wait=True)
        self.session = MagicMock()
        self.domains = self.session.__enter__.return_value.query.return_value.join.return_value.filter.return_value.distinct
        self.domains.return_value = [('phonea',)]
        self.patch_session = patch.object(evidence, 'session_scope', return_value=self.session)
        self.patch_session.start()
        self.addCleanup(self.patch_session.stop)
        self.task_id = str(uuid.uuid4())

    async def poll_and_drain(self):
        spawned = []
        create = asyncio.create_task
        def track(coroutine):
            task = create(coroutine)
            spawned.append(task)
            return task
        with patch.object(evidence.asyncio, 'create_task', side_effect=track):
            await evidence.poll()
        if spawned:
            await asyncio.gather(*spawned)
        return len(spawned)

    async def test_fresh_committed_terminal_wakes_old_failure_backoff_once(self):
        evidence.NEXT['phonea'] = time.monotonic() + 600
        with patch.object(evidence, 'sync', return_value=[]) as sync:
            self.assertEqual(await self.poll_and_drain(), 0)
            self.assertTrue(evidence.notify_terminal('phonea', self.task_id))
            self.assertEqual(await self.poll_and_drain(), 1)
            self.assertFalse(evidence.notify_terminal('phonea', self.task_id))
            self.assertEqual(await self.poll_and_drain(), 0)
            sync.assert_called_once_with('phonea')

    async def test_terminal_arriving_during_failed_read_is_not_lost(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def sync(domain):
            calls.append(domain)
            if len(calls) == 1:
                started.set()
                if not release.wait(3):
                    raise RuntimeError('test worker timeout')
                raise ConnectionError('old evidence read failed')
            return []
        with patch.object(evidence, 'sync', side_effect=sync):
            active = asyncio.create_task(evidence.perform('phonea'))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                self.assertTrue(await asyncio.to_thread(evidence.notify_terminal, 'phonea', self.task_id))
                self.assertEqual(await self.poll_and_drain(), 0)
            finally:
                release.set()
                await active
            self.assertEqual(evidence.NEXT['phonea'], 0)
            self.assertEqual(await self.poll_and_drain(), 1)
            self.assertEqual(calls, ['phonea', 'phonea'])
            self.assertEqual(await self.poll_and_drain(), 0)

    async def test_failed_urgent_attempt_keeps_normal_backoff_without_duplicate_storm(self):
        with patch.object(evidence, 'sync', side_effect=ConnectionError('offline')) as sync:
            evidence.notify_terminal('phonea', self.task_id)
            self.assertEqual(await self.poll_and_drain(), 1)
            self.assertGreater(evidence.NEXT['phonea'], time.monotonic() + 45)
            duplicates = await asyncio.gather(*[
                asyncio.to_thread(evidence.notify_terminal, 'phonea', self.task_id) for _ in range(20)])
            self.assertFalse(any(duplicates))
            self.assertEqual(await self.poll_and_drain(), 0)
            sync.assert_called_once()

    async def test_capacity_and_offline_wait_do_not_consume_pending_terminal_signal(self):
        evidence.notify_terminal('phonea', self.task_id)
        self.domains.return_value = []
        self.assertEqual(await self.poll_and_drain(), 0)
        self.domains.return_value = [('phonea',)]
        evidence.INFLIGHT.update({'phoneb', 'phonec'})
        self.assertEqual(await self.poll_and_drain(), 0)
        evidence.INFLIGHT.clear()
        with patch.object(evidence, 'sync', return_value=[]):
            self.assertEqual(await self.poll_and_drain(), 1)
        self.assertNotIn('phonea', evidence.WAKEUPS)

    async def test_invalid_terminal_identity_does_not_schedule_work(self):
        with patch.object(evidence, 'sync') as sync:
            for domain, task in [('../phone', self.task_id), ('phonea', 'invalid'), (None, self.task_id)]:
                self.assertFalse(evidence.notify_terminal(domain, task))
            evidence.NEXT['phonea'] = time.monotonic() + 600
            self.assertEqual(await self.poll_and_drain(), 0)
            sync.assert_not_called()


if __name__ == '__main__':
    unittest.main()
