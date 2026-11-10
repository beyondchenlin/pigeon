import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

class FrameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        class Handler:
            def __init__(self,authorized=True):self.authorized=authorized;self.calls=[];self.status=200
            def get_login_user_device(self,domain):
                self.calls.append('authorize')
                if not self.authorized:raise PermissionError()
                return None,types.SimpleNamespace(state='online')
            def get_argument(self,*args):return '360'
            def set_header(self,*args):pass
            def set_status(self,value):self.status=value
            def finish(self,*args):self.calls.append('finish')
        deps={'grpc':types.SimpleNamespace(),'PIL':types.SimpleNamespace(Image=object()),
              'lamda.client':types.SimpleNamespace(Device=object()),'server.service':types.SimpleNamespace(BaseHttpService=Handler),
              'server.bk_telemetry':types.SimpleNamespace(Deadline=object())}
        spec=importlib.util.spec_from_file_location('server.bk_frame',Path(__file__).parents[1]/'server/bk_frame.py')
        self.m=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,deps):spec.loader.exec_module(self.m)
        self.addCleanup(self.m.POOL.shutdown,wait=True)

    async def test_unauthorized_user_cannot_read_cached_frame(self):
        self.m.CACHE[('phone',360)]=(self.m.time.monotonic(),b'private')
        h=self.m.FrameHandler(False)
        with self.assertRaises(PermissionError):await h.get('phone')
        self.assertEqual(h.calls,['authorize'])

    async def test_serializes_device_and_bounds_global_queue(self):
        loop=asyncio.get_running_loop();pending=[]
        def launch(*args):f=loop.create_future();pending.append(f);return f
        with patch.object(loop,'run_in_executor',side_effect=launch):
            jobs=[asyncio.create_task(self.m.frame(str(i),360)) for i in range(4)]
            await asyncio.sleep(0);await asyncio.sleep(0)
            self.assertIsNone(await self.m.frame('0',720));self.assertIsNone(await self.m.frame('extra',360))
            shared=asyncio.create_task(self.m.frame('0',360));await asyncio.sleep(0)
            self.assertEqual(len(pending),4)
            for f in pending:f.set_result(b'image')
            self.assertEqual(await asyncio.gather(*jobs,shared),[b'image']*5)
            self.assertEqual(await self.m.frame('0',360),b'image');self.assertEqual(len(pending),4)
        self.assertEqual(self.m.PENDING,{})
