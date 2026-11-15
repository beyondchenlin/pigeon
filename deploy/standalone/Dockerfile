FROM node:24-alpine@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5 AS frontend
WORKDIR /build/vue
COPY vue/package.json vue/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY vue/ ./
RUN npm run build

FROM ghcr.io/fatedier/frps:v0.72.0@sha256:74a92b46be8cb287636c8fd238eed53c26c915ed51b95c4b430ad4620a247dc2 AS frp
FROM firerpa/pigeon:1.0@sha256:325fdf57f506ab02e819e2b7cb2d384d5b427ea34c5de2efd5f1d927a7b27e01
WORKDIR /opt/bk
ENV PYTHONPATH=/opt/bk PYTHONDONTWRITEBYTECODE=1 \
    BK_NATIVE_BUNDLE_DIR=/opt/bk/bridge BK_AI_DEVICES_FILE=/opt/bk/devices.json \
    BK_GATEWAY_LUA_DIR=/opt/bk/lua BK_GATEWAY_MANIFEST=/opt/bk/gateway.json \
    BK_CONSOLE_INDEX=/opt/bk/html/index.html BK_MAINTENANCE_DEVICE="" PIGEON_FORWARD_ONLY=1
COPY --from=frp /usr/bin/frps /usr/sbin/frps
RUN echo '4ad311e0630cf12945041143b926f515458836e896ff93c253d43c98cf304b89  /usr/sbin/frps' | sha256sum -c -
RUN python3 -m pip install --break-system-packages --no-cache-dir Pillow==13.0.0
COPY server/ ./server/
COPY executor/ ./executor/
# The upstream runtime already ships the exact repository LFS object.
RUN echo 'b9a4f94d557d57ceec49bc21d26d2e144cc5191b2d9e26989be89275c6aefea8  /usr/lib/python3/dist-packages/executor/IPDB.BIN' | sha256sum -c - \
    && ln -s /usr/lib/python3/dist-packages/executor/IPDB.BIN executor/IPDB.BIN
COPY lua/ ./lua/
COPY ops/install_bk_native_bridge.py ops/bk_ai_phone.py ops/bk_native_mcp.py ops/bk_ai_guard_extension.py ./bridge/
COPY ops/bk_native_restart_once.py ops/bk_service_lifecycle.py ops/bk_lifecycle_monitor.py ./bridge/
COPY deploy/pilot/wait_auth_then_frps.py ./
COPY deploy/standalone/devices.json deploy/standalone/bootstrap.py ./
COPY deploy/standalone/nginx.conf /usr/local/openresty/nginx/conf/nginx.conf
COPY deploy/standalone/supervisord.conf /etc/supervisord.conf
COPY --chmod=755 entry frps.sh mosquitto.sh /usr/local/bin/
RUN ln -s /usr/sbin/mosquitto /usr/local/bin/mosquitto-broker \
    && python3 bootstrap.py --make-manifest
COPY --from=frontend /build/vue/dist/ /opt/bk/html/
WORKDIR /user
CMD ["python3", "/opt/bk/bootstrap.py"]
