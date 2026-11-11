"""Owner-only, receipt-based switch for optional single-phone maintenance."""
import asyncio
import base64
import collections
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import time
from urllib.parse import urlsplit
import uuid

from server.service import BaseHttpService, Service
from .bk_ai_transport import mapping, TransportError

DOMAIN = 'umdax0nurk9o'
PUBLIC_ORIGIN = os.environ.get('BK_MAINTENANCE_ORIGIN','https://dongfengqi.top')
BASE = Path('/user/phone-pilot/maintenance')
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='bk-maintenance')


def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    try:
        with temp.open('x') as stream:
            os.chmod(temp,0o600);json.dump(value,stream)
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError,ValueError):
        return {} if default is None else default


def phone_call(domain, payload):
    import grpc
    from lamda.client import Device
    config = mapping(domain)
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    script = str(Path(config['bridge']).with_name('bk_maintenance.py'))
    command = 'export LD_LIBRARY_PATH='+shlex.quote(config['library'])+'; '+shlex.join([config['python'],script,encoded])
    class Deadline(grpc.UnaryUnaryClientInterceptor):
        def intercept_unary_unary(self, continuation, details, request):
            Call=collections.namedtuple('Call','method timeout metadata credentials wait_for_ready compression')
            return continuation(Call(details.method,12,details.metadata,details.credentials,False,None),request)
    device=Device('127.0.0.1',port=int(os.environ['API_PORT']),certificate=str(Path('/user/certificates')/(domain+'.pem')))
    device.channel=grpc.intercept_channel(device.channel,Deadline())
    try:
        reply=device.execute_script(command,timeout=10)
        if len(reply.stdout)>16000:
            raise ValueError()
        result=json.loads(reply.stdout)
        if type(result.get('enabled')) is not bool:
            raise ValueError()
        return result
    except Exception:
        raise TransportError('phone_state_unknown') from None
    finally:
        device.channel.close()


def effective(state, phone, host):
    wanted=state.get('enabled')
    request_id=state.get('request_id')
    if type(wanted) is not bool or not request_id:
        return 'unknown'
    phone_ok=(phone.get('request_id')==request_id and phone.get('receipt_id')==request_id
              and phone.get('phase')=='done' and phone.get('enabled') is wanted
              and phone.get('monitor_running') is wanted and phone.get('helper_running') is wanted)
    host_ok=(host.get('request_id')==request_id and host.get('enabled') is wanted
             and host.get('active') is wanted and not host.get('error')
             and (wanted or host.get('transport_disconnected') is True))
    if phone_ok and host_ok:
        return 'on' if wanted else 'off'
    if phone.get('phase')=='failed' or host.get('error') or time.time()-state.get('at',0)>60:
        return 'unknown'
    return 'changing'


def reconcile(domain, value=None):
    import fcntl
    if domain != DOMAIN:
        raise ValueError('unsupported_device')
    root=BASE/domain;root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (root/'operation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=read(root/'state.json')
        if value is not None:
            if state.get('request_id')==value['request_id']:
                if state.get('enabled') is not value['enabled']:
                    raise ValueError('request_reused')
                value=None  # Uncertain responses only query the existing receipt.
            elif state.get('effective')=='changing' and time.time()-state.get('at',0)<45:
                raise ValueError('transition_pending')
            else:
                state=dict(enabled=value['enabled'],request_id=value['request_id'],at=time.time(),effective='changing')
                save(root/'state.json',state)
                if not value['enabled']:
                    save(root/'host-request.json',state)
        try:
            phone=phone_call(domain,dict(action='set',**value) if value is not None else {'action':'status'})
        except TransportError as error:
            state.update(effective='unknown',error=error.code)
            save(root/'state.json',state)
            return public_state(state,{},read(root/'host-status.json'))
        # A valid receipt is sufficient to request host start; only actual
        # settled phone and host states can make the UI show "on" or "off".
        if state.get('request_id') and phone.get('request_id')==state['request_id']:
            host_request=read(root/'host-request.json')
            if host_request.get('request_id')!=state['request_id']:
                save(root/'host-request.json',dict(enabled=state['enabled'],request_id=state['request_id'],at=time.time()))
        host=read(root/'host-status.json')
        state.update(effective=effective(state,phone,host),error=phone.get('error') or host.get('error'))
        save(root/'state.json',state)
        return public_state(state,phone,host)


def public_state(state, phone, host):
    return {'enabled':state.get('enabled'),'request_id':state.get('request_id'),
            'effective':state.get('effective','unknown'),'error':state.get('error'),
            'phone':{key:phone.get(key) for key in ('monitor_running','helper_running','phase')},
            'cloud_active':host.get('active')}


class MaintenanceHandler(BaseHttpService):
    def prepare(self):
        self.set_header('Cache-Control','no-store')
        if self.request.method=='POST':
            origin=self.request.headers.get('Origin')
            # The deployed nginx forwards API Host as its upstream name. Match
            # the explicit public origin too, never trust forwarded headers.
            direct=self.request.protocol+'://'+self.request.host
            if origin and origin not in (PUBLIC_ORIGIN,direct):
                self.throw(403,'MAINTENANCE_ORIGIN','Cross-origin switch requests are not allowed')
            if self.request.headers.get('X-Requested-With')!='BK-Maintenance':
                self.throw(403,'MAINTENANCE_REQUEST','Explicit maintenance request required')

    async def handle(self, domain, value=None):
        self.get_login_user_device(domain)
        if domain!=DOMAIN:
            self.throw(404,'MAINTENANCE_DEVICE','Maintenance is not configured for this device')
        try:
            result=await asyncio.get_running_loop().run_in_executor(POOL,reconcile,domain,value)
        except ValueError as error:
            self.throw(409,'MAINTENANCE_BUSY',str(error))
        if value is not None:
            self.set_status(200 if result['effective'] in ('on','off') else 202)
        self.tell(result)

    async def get(self, domain):
        await self.handle(domain)

    async def post(self, domain):
        if len(self.request.body)>1000:
            self.throw(413,'MAINTENANCE_BODY','Request too large')
        try:
            value=json.loads(self.request.body)
            if not isinstance(value,dict) or set(value)!={'enabled','request_id'} or type(value['enabled']) is not bool:
                raise ValueError()
            if str(uuid.UUID(value['request_id']))!=value['request_id']:
                raise ValueError()
        except (ValueError,TypeError,KeyError,AttributeError,AssertionError):
            self.throw(400,'MAINTENANCE_BODY','Expected enabled and a request UUID')
        await self.handle(domain,value)


def install():
    if getattr(Service,'_bk_maintenance_installed',False):
        return
    original=Service.__init__
    def initialize(self,*args,**kwargs):
        original(self,*args,**kwargs)
        self.http.add_handler(r'/api/v1/device/([a-z0-9]+)/maintenance','server.bk_maintenance',handler='MaintenanceHandler')
    Service.__init__=initialize
    Service._bk_maintenance_installed=True
