"""High-level MCP stdio adapter to the existing authenticated PIGEON task API.

Run centrally over an authenticated SSH stdio connection for the single-phone
pilot. No new scheduler, phone credential, model key, or public HTTP listener.
MCP and its dependencies belong to this process, not the PIGEON API process.
"""
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any
from mcp.server.mcpserver.exceptions import ToolError


class ApiClient:
    def __init__(self, config):
        self.base = config['base_url'].rstrip('/')
        url = urllib.parse.urlsplit(self.base)
        if (url.scheme not in ('http', 'https') or url.username or url.password
                or url.path or url.query or url.fragment
                or (url.scheme == 'http' and url.hostname not in ('127.0.0.1', 'localhost', '::1'))):
            raise ValueError('Use an HTTPS PIGEON origin, or loopback HTTP')
        self.headers = {'X-User': config['user'], 'X-Auth': config['token'], 'Content-Type': 'application/json'}
        self.devices = frozenset(config['devices'])
        if not self.devices or any(not re.fullmatch(r'[a-z0-9]{1,64}', d) for d in self.devices):
            raise ValueError('An explicit device allowlist is required')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def tasks(self, device_id, task_id=None, action=None, body=None):
        if device_id not in self.devices:
            raise ToolError('Device is outside this MCP connection scope')
        path = '/api/v1/device/' + device_id + '/ai/tasks'
        if task_id is not None:
            canonical_uuid(task_id)
            path += '/' + task_id
        if action is not None:
            if action not in ('cancel', 'takeover', 'answer') or task_id is None:
                raise ToolError('Unsupported task action')
            path += '/' + action
        return self.request(path, body)

    def evidence(self, device_id):
        if device_id not in self.devices:
            raise ToolError('Device is outside this MCP connection scope')
        return self.request('/api/v1/device/' + device_id + '/ai/evidence')

    def request(self, path, body=None):
        request = urllib.request.Request(self.base + path, headers=self.headers,
            data=None if body is None else json.dumps(body).encode(), method='GET' if body is None else 'POST')
        # Never retry a creation request: an interrupted response is ambiguous.
        try:
            try:
                response = self.opener.open(request, timeout=35)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError('Oversized PIGEON response')
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError('Invalid PIGEON response')
                if response.code >= 400:
                    # A protocol-successful tools/call must not look like a
                    # successful dispatch when HTTP rejected/lost the request.
                    # Do not surface arbitrary backend bodies or retry writes.
                    raise ToolError('PIGEON returned HTTP %d; query the original task ID before any new submission'
                                    % response.code)
                return {'http_status': response.code, **result}
        except (OSError, ValueError):
            raise ToolError('PIGEON response unavailable; query the original task/request ID before resubmitting') from None


def canonical_uuid(value):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ToolError('A canonical UUID is required')
    return value


def create_server(api):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations
    server = MCPServer('PIGEON BK tasks', version='0.1.0', log_level='WARNING', instructions=(
        'Submit high-level goals; the phone executes its own agent loop. Save request_id before submission. '
        'The user only describes their goal: discover the authorized device, generate and persist the UUID yourself, '
        'and use the default supervision limit. Do not ask the user for tool names, account IDs, UUIDs or timeout values. '
        'Pass the user goal unchanged; execution and business reply rules are built into the phone. '
        'On an unknown response query the same deterministic task ID; never create a replacement automatically. '
        'A done task is NOT proof of a published video. Check platform evidence separately. '
        'The pilot has no fleet fan-out or automatic publish retries.'))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)

    @server.tool(annotations=read, structured_output=True)
    def list_devices() -> dict[str, Any]:
        """List device IDs configured for this MCP connection (not live readiness)."""
        return {'devices': sorted(api.devices)}

    @server.tool(annotations=read, structured_output=True)
    def list_tasks(device_id: str) -> dict[str, Any]:
        """Read the latest durable task receipts for an authorized device."""
        return api.tasks(device_id)

    @server.tool(annotations=read, structured_output=True)
    def list_evidence(device_id: str) -> dict[str, Any]:
        """Read private archived evidence metadata and sync state. Archived is not business-verified.

        Images remain behind PIGEON authentication at /api/v1/device/{device_id}/ai/evidence/{evidence_id}/image.
        This does not capture a new image, submit a task, or retry a business action.
        """
        return api.evidence(device_id)

    @server.tool(annotations=read, structured_output=True)
    def get_task(device_id: str, task_id: str) -> dict[str, Any]:
        """Reconcile one existing task; done describes execution, not business verification."""
        return api.tasks(device_id, task_id)

    @server.tool(annotations=write, structured_output=True)
    def submit_task(device_id: str, goal: str, request_id: str, timeout_s: int = 900) -> dict[str, Any]:
        """Run a high-level goal on one phone. Persist a fresh UUID first; reuse it for identical requests only.

        timeout_s is the current server supervision limit, not an offline phone deadline.
        No video publish policy or platform-specific verification is implied by this generic tool.
        """
        canonical_uuid(request_id)
        if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 4000:
            raise ToolError('goal must contain 1..4000 characters')
        if type(timeout_s) is not int or not 15 <= timeout_s <= 1800:
            raise ToolError('timeout_s must be an integer in 15..1800')
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'pigeon-bk:' + device_id + ':' + request_id))
        return {'task_id': task_id, 'request_id': request_id,
            **api.tasks(device_id, body={'goal': goal, 'request_id': request_id, 'timeout_s': timeout_s})}

    @server.tool(annotations=write, structured_output=True)
    def answer_task(device_id: str, task_id: str, question_key: str, text: str, request_id: str) -> dict[str, Any]:
        """Reply once to the current ordinary question. Read question_key from get_task; generate request_id yourself.

        Only convey the user's answer or a continuation already within their authorized scope.
        Never answer waiting_on_phone. On any ambiguous response, query the original task; do not resend.
        """
        canonical_uuid(request_id)
        if not re.fullmatch('[a-f0-9]{64}', question_key) or not 1 <= len(text.strip()) <= 2000:
            raise ToolError('A current question_key and 1..2000 character reply are required')
        return api.tasks(device_id, task_id, 'answer', dict(question_key=question_key, text=text, request_id=request_id))

    @server.tool(annotations=write, structured_output=True)
    def cancel_task(device_id: str, task_id: str) -> dict[str, Any]:
        """Request cancellation. Confirm a terminal receipt; acceptance alone does not prove it stopped."""
        return api.tasks(device_id, task_id, 'cancel', {})

    return server


if __name__ == '__main__':
    config = json.loads(Path(os.environ['PIGEON_MCP_CONFIG']).read_text(encoding='utf-8'))
    create_server(ApiClient(config)).run(transport='stdio')
