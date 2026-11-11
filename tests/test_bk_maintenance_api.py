import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch
if importlib.util.find_spec('sqlalchemy') is None or 'POSTGRES_PASSWORD' not in os.environ:
    raise unittest.SkipTest('Use existing PIGEON runtime')
from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application
from server import bk_maintenance as api


class MaintenanceApiTests(AsyncHTTPTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bk-maintenance-api-')
        self.remote={'enabled':False,'request_id':None,'phase':'not_applied','monitor_running':False,'helper_running':False}
        self.calls=[]
        def phone(domain,value):
            self.calls.append(value)
            if value['action']=='set':
                self.remote.update(enabled=value['enabled'],request_id=value['request_id'],phase='applying')
            return dict(self.remote)
        self.patches=[patch.object(api,'BASE',Path(self.temp.name)),patch.object(api,'phone_call',side_effect=phone)]
        for p in self.patches:p.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def get_app(self):
        class Handler(api.MaintenanceHandler):
            def get_login_user_device(self,domain):
                if self.request.headers.get('X-Test-Auth')!='owner':self.throw(401,'AUTH','unauthorized')
                return SimpleNamespace(id=1),SimpleNamespace(domain=domain)
        return Application([(r'/api/v1/device/([a-z0-9]+)/maintenance',Handler)],ctl=None)

    def request(self,value=None,headers=None,domain=api.DOMAIN):
        response=self.fetch('/api/v1/device/'+domain+'/maintenance',method='POST' if value is not None else 'GET',
                            body=json.dumps(value) if value is not None else None,
                            headers={'X-Test-Auth':'owner','X-Requested-With':'BK-Maintenance',**(headers or {})})
        return response.code,json.loads(response.body)

    def test_acknowledgement_is_not_enabled_until_both_sides_confirm(self):
        value=dict(enabled=True,request_id=str(uuid.uuid4()))
        code,reply=self.request(value)
        self.assertEqual(code,202);self.assertEqual(reply['data']['effective'],'changing')
        self.remote.update(phase='done',receipt_id=value['request_id'],monitor_running=True,helper_running=True)
        self.assertEqual(self.request()[1]['data']['effective'],'changing')
        api.save(api.BASE/api.DOMAIN/'host-status.json',dict(value,active=True))
        self.assertEqual(self.request()[1]['data']['effective'],'on')

    def test_timeout_and_duplicate_never_replay_phone_action(self):
        value=dict(enabled=False,request_id=str(uuid.uuid4()))
        with patch.object(api,'phone_call',side_effect=api.TransportError('phone_state_unknown')) as phone:
            self.assertEqual(self.request(value)[1]['data']['effective'],'unknown')
            self.request(value)
            self.assertEqual([c.args[1]['action'] for c in phone.call_args_list],['set','status'])
        self.assertFalse(api.read(api.BASE/api.DOMAIN/'host-request.json')['enabled'])

    def test_off_requires_actual_stopped_processes_and_disconnected_transport(self):
        value=dict(enabled=False,request_id=str(uuid.uuid4()))
        self.request(value)
        self.remote.update(phase='done',receipt_id=value['request_id'],monitor_running=False,helper_running=False)
        path=api.BASE/api.DOMAIN/'host-status.json'
        api.save(path,dict(value,active=False,transport_disconnected=False))
        self.assertNotEqual(self.request()[1]['data']['effective'],'off')
        api.save(path,dict(value,active=False,transport_disconnected=True))
        self.assertEqual(self.request()[1]['data']['effective'],'off')

    def test_authorization_origin_and_body_validation(self):
        value=dict(enabled=True,request_id=str(uuid.uuid4()))
        self.assertEqual(self.request(headers={'X-Test-Auth':'wrong'})[0],401)
        self.assertEqual(self.request(domain='differentphone')[0],404)
        self.assertEqual(self.request(value,headers={'Origin':'https://foreign.example'})[0],403)
        self.assertEqual(self.request(value,headers={'X-Requested-With':''})[0],403)
        self.assertEqual(self.request(dict(value,enabled='false'))[0],400)
        self.assertEqual(self.request(dict(value,command='anything'))[0],400)
        self.assertEqual(self.calls,[])

    def test_configured_public_origin_works_behind_upstream_host_rewrite(self):
        value=dict(enabled=False,request_id=str(uuid.uuid4()))
        code,result=self.request(value,headers={'Origin':'https://dongfengqi.top'})
        self.assertEqual(code,202)
        self.assertEqual(self.calls[0]['action'],'set')


if __name__=='__main__':unittest.main()
