"""Bounded JPEG fallback via the existing automation driver, not a new encoder."""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import io
import os
from pathlib import Path
import time

import grpc
from PIL import Image
from lamda.client import Device
from server.service import BaseHttpService
from .bk_telemetry import Deadline

POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='bk-frame')
PENDING = {}
CACHE = OrderedDict()
SIZES = (240, 360, 480, 720)


def capture(domain, short):
    d = Device('127.0.0.1', port=int(os.environ['API_PORT']),
               certificate=str(Path('/user/certificates')/(domain+'.pem')))
    d.channel = grpc.intercept_channel(d.channel, Deadline())
    try:
        # Existing UiAutomator process performs the capture; never starts the
        # native MJPEG/minicap path that can block during multithreaded fork.
        raw = d.screenshot(quality=65 if short==720 else 35).getvalue()
        if len(raw)>8*1024*1024:raise ValueError('Oversized device frame')
        with Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>24_000_000:raise ValueError('Oversized display')
            ratio=min(1,short/min(image.size))
            size=tuple(max(1,round(n*ratio)) for n in image.size)
            image=image.convert('RGB').resize(size,Image.Resampling.BILINEAR)
            out=io.BytesIO();image.save(out,format='JPEG',quality=75 if short==720 else 50)
            return out.getvalue()
    finally:d.channel.close()


async def frame(domain, short):
    key=(domain,short);cached=CACHE.get(key)
    if cached and time.monotonic()-cached[0]<.5:return cached[1]
    if key not in PENDING:
        # Per-device serialization and a global bound: no unbounded worker queue.
        if len(PENDING)>=4 or any(k[0]==domain for k in PENDING):return None
        async def run():
            try:
                data=await asyncio.get_running_loop().run_in_executor(POOL,capture,domain,short)
                CACHE[key]=(time.monotonic(),data);CACHE.move_to_end(key)
                while len(CACHE)>32:CACHE.popitem(last=False)
                return data
            except Exception:return None
            finally:PENDING.pop(key,None)
        PENDING[key]=asyncio.create_task(run())
    try:return await asyncio.wait_for(asyncio.shield(PENDING[key]),timeout=9)
    except asyncio.TimeoutError:return None


class FrameHandler(BaseHttpService):
    async def get(self, domain):
        _,device=self.get_login_user_device(domain)
        self.set_header('Cache-Control','no-store')
        try:short=int(self.get_argument('short','360'))
        except (ValueError,TypeError):short=0
        if short not in SIZES:self.set_status(400);self.finish();return
        if device.state!='online':self.set_status(503);self.finish();return
        data=await frame(domain,short)
        if data is None:self.set_status(503);self.finish();return
        self.set_header('Content-Type','image/jpeg');self.finish(data)
