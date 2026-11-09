-- Runs AFTER the existing gateway rewrite/authentication phase.
-- Only replace the HTML document; phone sockets, APIs and assets pass through.
if ngx.var.uri ~= '/d/' .. ngx.var.id .. '/' or ngx.var.arg_native == '1' then
    return
end

local page = io.open('/user/phone-pilot/frontend/current/index.html', 'rb')
          or io.open('/var/www/html/index.html', 'rb')
if not page then
    ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE)
end
local content = page:read('*a')
page:close()
ngx.header.content_type = 'text/html; charset=utf-8'
ngx.header['Cache-Control'] = 'no-store'
ngx.print(content)
ngx.exit(ngx.HTTP_OK)
