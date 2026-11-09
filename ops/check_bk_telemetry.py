"""Preflight inside the existing container, no service restart or phone changes."""
import asyncio
import json
import sys
from unittest.mock import patch
import server
server.__path__.append('/user/phone-pilot/bk-server')
from server import bk_telemetry as t

t.install()
t.install()
service = t.Service(18800)
assert sum('telemetry' in key for key in service.http.handlers) == 1


async def cache_checks():
    calls = []
    def read(domain):
        calls.append(domain)
        return {'sampled_at': t.time.time(), 'battery': {'percent': 0}, 'stale': False}
    with patch.object(t, 'collect', read):
        result = await asyncio.gather(t.sample('testcache'), t.sample('testcache'))
        assert len(calls) == 1 and result[0]['battery']['percent'] == 0
        await t.sample('testcache')
        assert len(calls) == 1
    t.CACHE['testcache']['sampled_at'] -= 60
    with patch.object(t, 'collect', side_effect=RuntimeError('offline')):
        result = await t.sample('testcache')
        assert result['stale'] and result['battery']['percent'] == 0
    t.CACHE.clear()
    t.PENDING.update({str(n): object() for n in range(4)})
    result = await t.sample('busytest')
    assert result['error'] == 'busy'
    t.PENDING.clear()
    class Unauthorized:
        def get_login_user_device(self, domain):
            raise PermissionError('not device owner')
    with patch.object(t, 'sample') as read:
        try:
            await t.TelemetryHandler.get(Unauthorized(), 'testcache')
        except PermissionError:
            pass
        else:
            raise AssertionError('ownership check missing')
        read.assert_not_called()


asyncio.run(cache_checks())
print('PASS: handler registration, coalescing, cache, stale failure, concurrency bound, ownership before sampling')
if len(sys.argv) > 1:
    print(json.dumps(t.collect(sys.argv[1]), ensure_ascii=False))
