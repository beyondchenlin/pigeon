"""Standard Mika MCP transport for the existing PIGEON phone bridge.

No Android changes, background service, new model, or credentials outside the
phone. The caller retains ownership checks, durable receipts and input leases.
Task submission is never retried or switched to HTTP after an uncertain result.
"""
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import uuid

MAX_RESPONSE = 1024 * 1024
PROTOCOL = '2025-06-18'
TASK = re.compile(r'/api/v1/tasks/([a-f0-9-]{36})(?:/(cancel|answer))?\Z')


def failure(code, status=502):
    return {'http': status, 'body': {'error': {'code': code}}}


class McpError(Exception):
    def __init__(self, code, status=502):
        super().__init__(code)
        self.code, self.status = code, status


class LocalTransport:
    """Only the credential's validated loopback port; redirects are forbidden."""
    def __init__(self, info_file):
        with open(info_file, encoding='utf-8') as stream:
            config = json.load(stream)
        port, token = config['port'], config['token']
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('invalid_port')
        if not isinstance(token, str) or not token or any(c in token for c in '\r\n'):
            raise ValueError('invalid_token')
        self.url = 'http://127.0.0.1:%d/mcp' % port
        self.headers = {'Authorization': 'Bearer ' + token,
                        'Content-Type': 'application/json',
                        'Accept': 'application/json, text/event-stream'}
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def __call__(self, message):
        request = urllib.request.Request(self.url, data=json.dumps(message).encode('utf-8'),
                                         headers=self.headers, method='POST')
        try:
            response = self.opener.open(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            if response.status in (401, 403):
                raise McpError('unauthorized', response.status)
            if response.status != 200:
                raise McpError('delivery_unknown')
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise McpError('invalid_mcp_response')
            return json.loads(raw)


class MikaMcp:
    def __init__(self, transport):
        self.transport = transport
        self.sequence = 0

    def rpc(self, method, params):
        self.sequence += 1
        reply = self.transport({'jsonrpc': '2.0', 'id': self.sequence,
                                'method': method, 'params': params})
        if (not isinstance(reply, dict) or reply.get('jsonrpc') != '2.0'
                or type(reply.get('id')) is not int or reply['id'] != self.sequence
                or 'error' in reply or not isinstance(reply.get('result'), dict)):
            raise McpError('invalid_mcp_response')
        return reply['result']

    def initialize(self):
        result = self.rpc('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
            'clientInfo': {'name': 'pigeon-mika-adapter', 'version': '1.0'}})
        if result.get('protocolVersion') != PROTOCOL:
            raise McpError('unsupported_mcp_protocol')
        return result

    def tools(self):
        return self.rpc('tools/list', {})

    def call(self, name, arguments):
        result = self.rpc('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError') is not False:
            # Do not infer safe retry from human-readable error messages.
            raise McpError('mcp_tool_result_unknown')
        data = result.get('structuredContent')
        if not isinstance(data, dict):
            content = result.get('content', [])
            if len(content) != 1 or content[0].get('type') != 'text':
                raise McpError('invalid_mcp_response')
            data = json.loads(content[0]['text'])
        if not isinstance(data, dict):
            raise McpError('invalid_mcp_response')
        return data


def probe(info, package='com.beyondchenlin.bk101.nativemcp',
          transport_factory=LocalTransport, package_check=None):
    """Read-only discovery; never return the connection file or its secrets."""
    if package != 'com.beyondchenlin.bk101.nativemcp':
        return {'state': 'unsupported_package'}
    if package_check is None:
        def package_check(name):
            result = subprocess.run(['pm', 'list', 'packages', name], capture_output=True,
                                    timeout=3, check=False)
            if result.returncode != 0:
                raise OSError('package_check_failed')
            return ('package:' + name).encode() in result.stdout.splitlines()
    try:
        if not package_check(package):
            return {'state': 'not_installed'}
        if not os.path.isfile(info):
            return {'state': 'not_ready'}
        client = MikaMcp(transport_factory(info))
        initialized = client.initialize()
        identity = initialized.get('serverInfo')
        if (not isinstance(identity, dict) or identity.get('name') != 'mika'
                or identity.get('title') != 'BK CLAW'
                or not isinstance(identity.get('version'), str)):
            return {'state': 'incompatible'}
        result = client.tools()
        tools = result.get('tools')
        if not isinstance(tools, list) or any(not isinstance(t, dict) for t in tools):
            return {'state': 'incompatible'}
        required = {'mika_run_task', 'mika_task_status', 'mika_list_tasks',
                    'mika_cancel_task', 'mika_answer', 'mika_device_state'}
        if not required.issubset({t.get('name') for t in tools}):
            return {'state': 'incompatible'}
        return {'state': 'connected', 'version': identity['version'][:80]}
    except McpError as exc:
        return {'state': 'auth_failed' if exc.status in (401, 403) else 'not_ready'}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return {'state': 'not_ready'}


def task_operation(method, path, body):
    """Translate only existing task operations, not arbitrary MCP tools."""
    if method == 'GET' and path == '/api/v1/device' and body is None:
        return 'mika_device_state', {}, None
    if method == 'POST' and path == '/api/v1/tasks':
        if isinstance(body, dict) and 'execution' in body:
            raise McpError('unsupported_execution', 400)
        if (not isinstance(body, dict) or set(body) - {'goal', 'request_id', 'conversation'}
                or not isinstance(body.get('goal'), str) or not body['goal'].strip()):
            raise McpError('invalid_argument', 400)
        task_id = body.get('request_id')
        if not isinstance(task_id, str) or str(uuid.UUID(task_id)) != task_id:
            raise McpError('invalid_argument', 400)
        return 'mika_run_task', dict(body), task_id
    if method == 'GET' and path == '/api/v1/tasks':
        return 'mika_list_tasks', {'limit': 50}, None
    match = TASK.fullmatch(path)
    if match:
        task_id, action = match.groups()
        if str(uuid.UUID(task_id)) != task_id:
            raise McpError('invalid_argument', 400)
        if method == 'GET' and action is None:
            return 'mika_task_status', {'task_id': task_id}, task_id
        if method == 'POST' and action == 'cancel' and body in (None, {}):
            return 'mika_cancel_task', {'task_id': task_id}, task_id
        if (method == 'POST' and action == 'answer' and isinstance(body, dict)
                and set(body) == {'text'} and isinstance(body['text'], str)):
            return 'mika_answer', {'task_id': task_id, 'text': body['text']}, task_id
    raise McpError('unsupported_operation', 400)


def task_api(info, method, path, body=None, transport_factory=LocalTransport):
    try:
        try:
            tool, arguments, task_id = task_operation(method, path, body)
        except (TypeError, ValueError, AttributeError):
            return failure('invalid_argument', 400)
        client = MikaMcp(transport_factory(info))
        client.initialize()
        result = client.call(tool, arguments)
        if task_id is not None and result.get('task_id') != task_id:
            raise McpError('mcp_task_identity_mismatch')
        if tool == 'mika_cancel_task':
            # cancelled is acceptance, not necessarily the terminal state.
            result = dict(result, accepted=result.get('cancelled') is True)
        return {'http': 200, 'body': result}
    except FileNotFoundError:
        return failure('phone_access_disabled', 409)
    except McpError as exc:
        return failure(exc.code, exc.status)
    except Exception:
        return failure('delivery_unknown')
