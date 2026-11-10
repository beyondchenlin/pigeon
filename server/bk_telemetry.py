"""Authenticated, bounded live telemetry extension for the existing PIGEON API."""
import asyncio
import collections
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import grpc
from google.protobuf.json_format import MessageToDict
from lamda.client import Device
from server.service import BaseHttpService, Service
from .bk_metrics import READ_COMMAND, parse_metrics

POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='bk-telemetry')
CACHE = collections.OrderedDict()
PENDING = {}
MAX_CACHE = 128


class Deadline(grpc.UnaryUnaryClientInterceptor):
    def intercept_unary_unary(self, continuation, details, request):
        Call = collections.namedtuple('Call', 'method timeout metadata credentials wait_for_ready compression')
        bounded = Call(details.method, min(details.timeout or 8, 8), details.metadata, details.credentials,
                       getattr(details, 'wait_for_ready', None), getattr(details, 'compression', None))
        return continuation(bounded, request)


def collect(domain):
    certificate = Path('/user/certificates') / (domain + '.pem')
    device = Device('127.0.0.1', port=int(os.environ['API_PORT']), certificate=str(certificate))
    device.channel = grpc.intercept_channel(device.channel, Deadline())
    try:
        result = device.execute_script(READ_COMMAND, timeout=5)
        data = parse_metrics(result.stdout)
        if data['battery']['percent'] is None and not data['system']:
            raise ValueError('No system telemetry returned')
        info = MessageToDict(device.device_info(), preserving_proto_field_name=True)
        data['device'] = {k: v for k, v in info.items() if k in (
            'screenOn', 'screenLocked', 'displayWidth', 'displayHeight', 'displayRotation', 'productName',
            'screen_on', 'screen_locked', 'display_width', 'display_height', 'display_rotation')}
        data.update(sampled_at=time.time(), source='android-system', stale=False)
        return data
    finally:
        device.channel.close()


async def sample(domain):
    cached = CACHE.get(domain)
    if cached and time.time() - cached['sampled_at'] < 10:
        return cached
    if domain not in PENDING:
        if len(PENDING) >= 4:
            return dict(cached or {}, stale=True, error='busy')
        async def run():
            try:
                data = await asyncio.get_running_loop().run_in_executor(POOL, collect, domain)
                CACHE[domain] = data
                CACHE.move_to_end(domain)
                while len(CACHE) > MAX_CACHE:
                    CACHE.popitem(last=False)
                return data
            except Exception:
                return dict(CACHE.get(domain) or {}, stale=True, error='unavailable')
            finally:
                PENDING.pop(domain, None)
        PENDING[domain] = asyncio.create_task(run())
    try:
        return await asyncio.wait_for(asyncio.shield(PENDING[domain]), timeout=12)
    except asyncio.TimeoutError:
        return dict(cached or {}, stale=True, error='timeout')


class TelemetryHandler(BaseHttpService):
    async def get(self, domain):
        # Recheck device ownership before every read, including cached results.
        _, device = self.get_login_user_device(domain)
        self.set_header('Cache-Control', 'no-store')
        if device.state != 'online':
            self.tell({'stale': True, 'error': 'offline'})
            return
        self.tell(await sample(domain))


def install():
    if getattr(Service, '_bk_telemetry_installed', False):
        return
    original = Service.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.http.add_handler(r'/api/v1/device/([a-z0-9]+)/telemetry', 'server.bk_telemetry', handler='TelemetryHandler')
        self.http.add_handler(r'/api/v1/device/([a-z0-9]+)/ice', 'server.bk_ice', handler='IceHandler')
        self.http.add_handler(r'/api/v1/device/([a-z0-9]+)/frame', 'server.bk_frame', handler='FrameHandler')
    Service.__init__ = initialize
    Service._bk_telemetry_installed = True
