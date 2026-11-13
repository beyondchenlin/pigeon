"""MCP SDK handshake/tools/call against a local fake PIGEON API, no phone writes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
import unittest
import uuid

if importlib.util.find_spec('mcp') is None:
    raise unittest.SkipTest('Run MCP contract tests in the isolated ops/requirements-mcp.txt environment')
from mcp import Client

SPEC = importlib.util.spec_from_file_location('bk_mcp', Path(__file__).parents[1] / 'ops/bk_mcp.py')
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


class McpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self.response_status = 200
        test = self
        requests = self.requests
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.reply()
            def do_POST(self):
                self.reply()
            def reply(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                requests.append((self.command, self.path, json.loads(body) if body else None))
                if self.headers.get('X-User') != 'pilot' or self.headers.get('X-Auth') != 'test-token':
                    self.send_error(401); return
                data = json.dumps({'data': {'status': 'done', 'result': {'answer': 'unverified'}}}).encode()
                self.send_response(test.response_status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.api = adapter.ApiClient({'base_url': 'http://127.0.0.1:%d' % self.http.server_port,
            'user': 'pilot', 'token': 'test-token', 'devices': ['phonea']})

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(timeout=2)

    async def test_protocol_and_exact_forwarding_without_fake_verification(self):
        async with Client(adapter.create_server(self.api)) as client:
            tools = await client.list_tools()
            self.assertIn('submit_task', [tool.name for tool in tools.tools])
            request_id = str(uuid.uuid4())
            result = await client.call_tool('submit_task', {'device_id': 'phonea', 'goal': 'read settings',
                'request_id': request_id, 'timeout_s': 60})
            self.assertFalse(result.is_error)
            body = result.structured_content
            self.assertEqual(body['task_id'], str(uuid.uuid5(uuid.NAMESPACE_URL, 'pigeon-bk:phonea:' + request_id)))
            self.assertNotIn('verified', body['data']['result'])
            self.assertEqual(self.requests, [('POST', '/api/v1/device/phonea/ai/tasks',
                {'goal': 'read settings', 'request_id': request_id, 'timeout_s': 60})])

    async def test_long_budget_boundaries_and_default_forward_exactly(self):
        async with Client(adapter.create_server(self.api)) as client:
            for timeout in (15, 7200, 10800, None):
                args = dict(device_id='phonea', goal='read only', request_id=str(uuid.uuid4()))
                if timeout is not None:
                    args['timeout_s'] = timeout
                result = await client.call_tool('submit_task', args)
                self.assertFalse(result.is_error)
                self.assertEqual(self.requests[-1][2]['timeout_s'], timeout or 900)
        self.assertEqual(len(self.requests), 4)

    async def test_browse_contract_forwarded_without_default_fifteen_minute_timeout(self):
        execution = dict(version=1, kind='browse', duration_s=7200, clock='elapsed',
            finish_timeout_s=60, stop_timeout_s=30, progress_interval_s=600,
            event_latency_s=30, budget={'max_tokens': 100000},
            progress_requirements={'min_observations': 2, 'min_active_s': 7000, 'max_stall_s': 120})
        async with Client(adapter.create_server(self.api)) as client:
            request_id = str(uuid.uuid4())
            args = dict(device_id='phonea', goal='read only', request_id=request_id, execution=execution)
            result = await client.call_tool('submit_task', args)
            self.assertFalse(result.is_error)
            self.assertEqual(self.requests[-1][2], dict(goal='read only', request_id=request_id, execution=execution))
            result = await client.call_tool('submit_task', dict(args, timeout_s=8000))
            self.assertFalse(result.is_error)
            self.assertEqual(self.requests[-1][2]['timeout_s'], 8000)
            self.assertEqual(self.requests[-1][2]['execution'], execution)

    async def test_out_of_range_budgets_do_not_reach_backend(self):
        async with Client(adapter.create_server(self.api)) as client:
            for timeout in (14, 10801):
                result = await client.call_tool('submit_task', dict(device_id='phonea', goal='read only',
                    request_id=str(uuid.uuid4()), timeout_s=timeout))
                self.assertTrue(result.is_error)
        self.assertEqual(self.requests, [])

    async def test_scope_and_validation_do_not_reach_backend(self):
        async with Client(adapter.create_server(self.api)) as client:
            for tool, args in [('list_tasks', {'device_id': 'phoneb'}), ('list_evidence', {'device_id':'phoneb'}),
                ('get_task', {'device_id': 'phonea', 'task_id': '../wrong'}),
                ('submit_task', {'device_id': 'phonea', 'goal': 'x', 'request_id': str(uuid.uuid4()), 'timeout_s': 10801})]:
                result = await client.call_tool(tool, args)
                self.assertTrue(result.is_error)
            self.assertEqual(self.requests, [])

    async def test_explicit_get_task_refreshes_but_list_remains_cached(self):
        task_id = str(uuid.uuid4())
        async with Client(adapter.create_server(self.api)) as client:
            result = await client.call_tool('get_task', {'device_id': 'phonea', 'task_id': task_id})
            self.assertFalse(result.is_error)
            result = await client.call_tool('list_tasks', {'device_id': 'phonea'})
            self.assertFalse(result.is_error)
        self.assertEqual(self.requests, [
            ('GET', '/api/v1/device/phonea/ai/tasks/' + task_id + '?refresh=1', None),
            ('GET', '/api/v1/device/phonea/ai/tasks', None)])

    async def test_evidence_is_read_only_and_does_not_submit(self):
        async with Client(adapter.create_server(self.api)) as client:
            result = await client.call_tool('list_evidence', {'device_id':'phonea'})
            self.assertFalse(result.is_error)
            self.assertEqual(self.requests,[('GET','/api/v1/device/phonea/ai/evidence',None)])

    async def test_answer_uses_existing_task_and_current_question_without_new_task(self):
        task_id, request_id = str(uuid.uuid4()), str(uuid.uuid4())
        async with Client(adapter.create_server(self.api)) as client:
            result = await client.call_tool('answer_task', dict(device_id='phonea', task_id=task_id,
                request_id=request_id, question_key='a'*64, text='Continue reading only'))
            self.assertFalse(result.is_error)
        self.assertEqual(self.requests, [('POST', '/api/v1/device/phonea/ai/tasks/' + task_id + '/answer',
            dict(question_key='a'*64, text='Continue reading only', request_id=request_id))])

    async def test_backend_failure_is_tool_error_without_retry(self):
        self.response_status = 503
        async with Client(adapter.create_server(self.api)) as client:
            result = await client.call_tool('submit_task', {'device_id': 'phonea', 'goal': 'read settings',
                'request_id': str(uuid.uuid4()), 'timeout_s': 60})
            self.assertTrue(result.is_error)
            self.assertEqual(len(self.requests), 1)
            self.assertIn('original task ID', str(result.content))

    def test_remote_plaintext_and_url_credentials_are_rejected(self):
        for url in ('http://example.com', 'https://u:p@example.com', 'https://example.com/path'):
            with self.assertRaises(ValueError):
                adapter.ApiClient({'base_url': url, 'user': 'u', 'token': 't', 'devices': ['phonea']})


if __name__ == '__main__':
    unittest.main()
