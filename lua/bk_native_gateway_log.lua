-- The HTTP request and any upgraded WebSocket have finished. If this bounded
-- cleanup cannot run, its durable record remains and reload stays blocked.
local connection = ngx.ctx.bk_gateway_connection
if not connection then return end
local function close_record(premature, domain, id, secret_file, api_url)
    if premature then return end
    if not api_url or not api_url:match('^http://127%.0%.0%.1:%d+/__bk_gateway$') then return end
    local file = io.open(secret_file, 'rb')
    if not file then return end
    local key = file:read('*a'); file:close()
    key = key and key:match('^%s*([0-9a-f]+)%s*$')
    if not key or #key ~= 64 then return end
    local http = require 'resty.http'
    local cjson = require 'cjson.safe'
    local client = http.new(); client:set_timeout(2000)
    local reply = client:request_uri(api_url, {
        method='POST', headers={['X-BK-Gateway-Key']=key, ['Content-Type']='application/json'},
        body=cjson.encode({action='close',domain=domain,connection_id=id}),
    })
    if not reply or reply.status ~= 204 then
        ngx.log(ngx.ERR, 'BK gateway close unconfirmed; reload remains blocked')
    end
end
local ok = ngx.timer.at(0, close_record, connection.domain, connection.connection_id,
    ngx.var.bk_gateway_secret_file, ngx.var.bk_gateway_api)
if not ok then ngx.log(ngx.ERR, 'BK gateway close could not be scheduled; reload remains blocked') end
