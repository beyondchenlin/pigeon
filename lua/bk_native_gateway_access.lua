-- Called after the existing device authentication rewrite. Fail closed: an
-- uncertain registration never forwards the request to the phone.
local cjson = require 'cjson.safe'
local file = io.open(ngx.var.bk_gateway_secret_file, 'rb')
if not file then return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE) end
local key = file:read('*a'); file:close()
key = key and key:match('^%s*([0-9a-f]+)%s*$')
if not key or #key ~= 64 then return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE) end
local raw = ngx.var.request_id
if not raw or not raw:match('^[0-9a-f]+$') or #raw ~= 32 then
    return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE)
end
local id = raw:sub(1,8)..'-'..raw:sub(9,12)..'-'..raw:sub(13,16)..'-'..raw:sub(17,20)..'-'..raw:sub(21,32)
local domain = ngx.var.id
if not domain or not domain:match('^[a-z0-9]+$') then return ngx.exit(ngx.HTTP_BAD_REQUEST) end
local statfile = io.open('/proc/self/stat', 'rb')
if not statfile then return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE) end
local stat = statfile:read('*a'); statfile:close()
local rest = stat and stat:match('^%d+ %b() (.+)$')
local start, n = nil, 0
if rest then
    for field in rest:gmatch('%S+') do
        n = n + 1
        if n == 20 then start = field; break end
    end
end
if not start or not start:match('^%d+$') then return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE) end
ngx.var.bk_gateway_key = key
local reply = ngx.location.capture('/_bk_gateway_internal', {
    method = ngx.HTTP_POST,
    copy_all_vars = true,
    body = cjson.encode({action='open', domain=domain, connection_id=id,
                         worker_pid=ngx.worker.pid(), worker_start=start}),
})
ngx.var.bk_gateway_key = ''
if not reply or reply.status ~= 204 then
    return ngx.exit(reply and reply.status == 409 and 409 or ngx.HTTP_SERVICE_UNAVAILABLE)
end
ngx.ctx.bk_gateway_connection = {domain=domain, connection_id=id}

-- Preserve the existing HTML replacement and leave its authorization intact.
return dofile(ngx.var.bk_device_console_file)
