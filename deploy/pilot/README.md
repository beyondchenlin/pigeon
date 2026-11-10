# 现有单机试点部署快照

本目录保存 2026-11-11 从现有服务器核对取回的非敏感配置及兼容代码。它用于追溯当前试点，**不是可直接用于新服务器或 10–100 台设备的一键部署模板**。本次只补齐版本管理，没有修改或重启线上服务。

线上采用 `firerpa/pigeon:1.0` 原始镜像，加挂 BK 扩展、前端构建和单机资源配置。镜像身份及文件 SHA-256 见 [核对记录](../../docs/DEPLOYMENT-AUDIT-20261111.md) 和 [清单](./audit-20261111.json)。Compose 保留了现场镜像标签；标签本身可能变化，不能把它当成可复现的镜像锁定。

## 文件对应关系

服务器部署根目录：`/opt/phone-pilot-20261108/pigeon`。

| 本目录文件 | 现有服务器位置或作用 |
| --- | --- |
| `compose.json` | 部署根目录的 Compose 配置；单 API 进程、单执行 worker 及资源上限 |
| `single-phone-supervisord.conf` | 容器 `/etc/supervisord.conf`，加载 BK API、事件兼容和 FRP 启动器 |
| `single-phone-nginx.conf` | 容器 `/usr/local/openresty/nginx/conf/nginx.conf`，前端与设备页面代理 |
| `auth.single-phone.py` | 挂载到容器 `executor/auth.py`；与仓库规范源码只差空行 |
| `heartbeat_compat.py` | 容器 `/user/phone-pilot/heartbeat_compat.py`，10.9 手机心跳与旧镜像兼容 |
| `wait_auth_then_frps.py` | 容器 `/user/phone-pilot/wait_auth_then_frps.py`，等待本地鉴权端口后启动 FRP |
| `postgres.conf`、`rabbitmq.conf`、`redis/*.sh` | 部署根目录同名配置/脚本，挂载给各数据服务 |

`heartbeat_compat.py` **只针对当前试点设备域** `umdax0nurk9o`。保留原有任务处理，只接受主题与设备身份匹配、未保留、时间有效的指定心跳事件；它不能代替多设备接入设计。迁移到其他设备前需要单独修改、验证作用域。

## 其余代码与私有部署依赖

- BK API 扩展的规范源码在 `server/bk_*.py`；安装、检查和启动代码在 `ops/`。
- 设备页面代理扩展在 `lua/bk_device_console.lua`；FRP 启动脚本是仓库根目录 `frps.sh`。
- `ops/run_bk_server.py` 与 `ops/install_bk_telemetry.py` 负责旧镜像扩展加载；不要为了入口文件逐字一致而覆盖现有 `server/__main__.py`。
- 前端从 `vue/` 构建，线上通过 `frontend/current` 指向已验证构建；不把 `vue/dist` 或发布压缩包放入 Git。
- 现有 `/user/phone-pilot/` 挂载还需已安装的 BK 源码、前端、FRP 0.72.0 二进制及相应依赖。本目录没有打包运行环境。
- `.env`、设备凭据、证书/私钥、数据库、用户数据和备份均在服务器私有存储中，**不包含在本目录**。Compose 只引用必填环境变量，不提供密码默认值。主机外层域名/TLS 代理也不属于此容器快照。

恢复现有部署时，应先核对本清单和私有依赖，在隔离环境验证迁移，再使用项目原有部署流程；不要在生产目录直接执行不经核对的 `docker compose up`。

## 检查

```sh
python -B -m unittest discover -s tests -p test_bk_heartbeat_compat.py -v
```

这组测试从试点已有的 8 项心跳验证用例迁入，覆盖旧/保留消息、主题或身份不匹配、无效时间以及断开转发不能续租等边界。配置语法和线上服务检查记录在核对文档中。
