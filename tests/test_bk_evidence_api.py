import json
import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from test_bk_ai_api import AiApiTests
from server import bk_evidence
from server.bk_evidence_store import Archive


class EvidenceApiTests(AiApiTests):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bk-evidence-api-')
        self.store = Archive(self.temp.name)
        super().setUp()
        self.epatches = [patch.object(bk_evidence,'session_scope',self.sessions), patch.object(bk_evidence,'archive',return_value=self.store)]
        for p in self.epatches: p.start()

    def tearDown(self):
        for p in reversed(self.epatches): p.stop()
        super().tearDown()
        self.temp.cleanup()

    def get_app(self):
        app = super().get_app()
        base = self
        class EvidenceHandler(bk_evidence.EvidenceHandler):
            def get_login_user_device(self, domain):
                auth = self.request.headers.get('X-Test-Auth')
                if auth not in ('owner','other'): self.throw(401,'AUTH','unauthorized')
                return SimpleNamespace(id=1 if auth == 'owner' else 2), SimpleNamespace(id=1 if domain == 'phonea' else 2)
        app.add_handlers('.*$', [(r'/api/v1/device/([a-z0-9]+)/ai/evidence(?:/([a-f0-9-]{36})/image)?',EvidenceHandler)])
        return app

    def test_evidence_list_and_image_require_task_owner_even_on_shared_device(self):
        import hashlib
        _, (_, response) = self.submit()
        eid = str(uuid.uuid4()); data = b'\xff\xd8test\xff\xd9'
        meta = dict(evidence_id=eid,task_id=response['data']['task_id'],size=len(data),sha256=hashlib.sha256(data).hexdigest(),mime='image/jpeg')
        self.store.put('phonea',meta,data)
        path = '/api/v1/device/phonea/ai/evidence'
        self.assertEqual(self.fetch(path).code,401)
        self.assertEqual(self.fetch(path+'/'+eid+'/image').code,401)
        self.assertEqual(json.loads(self.fetch(path,headers={'X-Test-Auth':'other'}).body)['data']['evidence'],[])
        self.assertEqual(self.fetch(path+'/'+eid+'/image',headers={'X-Test-Auth':'other'}).code,404)
        reply = self.fetch(path+'/'+eid+'/image',headers={'X-Test-Auth':'owner'})
        self.assertEqual(reply.body,data)
        self.assertEqual(reply.headers['Cache-Control'],'private, no-store')
