-- Loopback-only attestation endpoint. It does not expose the private key.
local file = io.open('/user/phone-pilot/bk-gateway-secret', 'rb')
if not file then return ngx.exit(503) end
local key = file:read('*a'); file:close()
key = key and key:match('^%s*([0-9a-f]+)%s*$')
if not key or #key ~= 64 then return ngx.exit(503) end
if ngx.req.get_headers()['X-BK-Gateway-Key'] ~= key then return ngx.exit(403) end
ngx.header['Cache-Control'] = 'no-store'
ngx.header.content_type = 'application/json'
ngx.say(require('cjson.safe').encode({worker_pid=ngx.worker.pid(), version=1}))
