# 在新电脑或服务器上部署 BK 群控

BK 使用 Pigeon 原有的 Docker Compose 架构。应用镜像从仓库里的 Vue 前端、Python 服务、设备网关和四个手机桥文件构建。基础镜像、FRP、数据库和消息服务按镜像摘要固定。首次启动会生成实例自己的管理员、设备签发密钥和网关密钥。原服务器可以继续运行。

## 选择安装方式

准备可运行 Linux amd64 容器的 Docker、Compose v2 或更新版本和 Python 3。Windows x64 电脑可以使用 Docker Desktop 的 Linux 容器模式。Linux x86-64 服务器可以使用 Docker Engine 和 Compose 插件。安装时需要访问 Docker Hub 和 GitHub Container Registry。手机从外网接入家里的电脑时，路由器也要能把相应端口转发给这台电脑。

从[里程碑发布页](https://github.com/beyondchenlin/pigeon/releases/tag/bk-v0.5.1-standalone)下载并解压 `BK-Pigeon-0.5.1-standalone.zip`，进入包内的 `deploy/standalone` 目录。也可以从同一 Git tag 的完整源码进入该目录。默认应用镜像是 `ghcr.io/beyondchenlin/bk-pigeon:0.5.1-standalone`。发布页列有不可变摘要，需要锁定镜像时可在生成的 `.env` 中设置 `BK_IMAGE=ghcr.io/beyondchenlin/bk-pigeon@sha256:完整摘要`。

如需单独拉取 BK 应用镜像，可以执行：

```sh
docker pull ghcr.io/beyondchenlin/bk-pigeon:0.5.1-standalone
```

以下地址是示例，请换成**手机实际能访问的地址**。Windows 如使用 Python Launcher，可把 `python3` 换成 `py -3`。

**局域网电脑**，例如手机和电脑都在同一个 Wi-Fi 下

```sh
python3 configure.py --public-ip 192.168.1.30
docker compose pull
docker compose up -d --no-build --wait
```

浏览器打开 `https://192.168.1.30:8000`。此入口使用实例自行生成的证书。需受信任 HTTPS 的浏览器功能可使用下方域名入口，或接入自己信任的 TLS 代理。

**公网服务器**，例如域名指向服务器公网 IPv4，同时启用 HTTPS 与 WebRTC 中继

```sh
python3 configure.py --public-ip 203.0.113.10 --domain phones.example.com --https --turn
docker compose pull
docker compose up -d --no-build --wait
```

域名解析到本机、80 和 443 可从公网到达后，Caddy 会申请证书，浏览器访问 `https://phones.example.com`。服务器位于云 NAT 后时可以加 `--lan-ip 内网IPv4`。已有 HTTPS 反向代理时可用 `--origin https://phones.example.com` 替代 `--https`，由原代理把网页及 WebSocket 转到本实例的 Web 端口。请使用单独的域名和空数据卷试装。

| 入口 | 默认端口 | 谁使用 |
| --- | --- | --- |
| Web 和设备初始配置 | TCP 8000 | 浏览器直连，手机访问后台生成的 properties 地址。使用域名代理后手机仍需访问这个端口 |
| FRP | TCP 7000，QUIC 使用同号 UDP | 手机主动连接本实例 |
| MQTT | TCP 8883 | 手机使用本实例签发的凭据上报状态 |
| 自动 HTTPS | TCP 80、443 | 启用 `--https` 时使用 |
| TURN | TCP/UDP 3478，UDP 49160–49200 | 启用 `--turn` 时使用，中继 UDP 端口需按原端口映射到主机 |

可在首次配置时使用 `--web-port`、`--frp-port`、`--mqtt-port`。数据库、队列、Redis 和设备控制 API 都在实例内部网络。云安全组、防火墙和路由器规则由主机管理员设置。

## 第一次登录和接手机

1. 登录名为 `admin`。初始密码保存在本目录私有的 `.env` 文件中，对应 `PIGEON_INITIAL_PASSWORD`。妥善保存整份文件，它也包含实例名称和数据服务密码。再次启动使用同一份文件。
2. 在“设备”中新增“转发”设备。在该手机安装兼容的 FIRERPA、完成授权，应用这条记录生成的专属配置。网页侧栏的“手机安装与接入说明”会打开本实例的指南。
3. 手机显示在线后，检查竖屏和横屏画面、底部导航、点击和滑动，再切换右侧标签继续操作。WebRTC 增强功能取决于手机 FIRERPA 版本和 RTC 扩展。兼容性校验与安装入口在 `ops/install_bk_rtc.py`，原生 WebSocket 是另一条可验收的传输路径。
4. 使用 AI 任务时，在手机安装兼容的 BK CLAW，配置模型、开启 MCP 并授予必要的系统权限。BK CLAW 0.1.0 已在单台 vivo 上验证。按下方步骤登记后，服务器会在手机空闲时自动安装或更新桥接。
5. 外部 MCP 客户端可在其自己的 Python 环境安装 `ops/requirements-mcp.txt`，将 `PIGEON_MCP_CONFIG` 指向私有 JSON 文件，再用该环境的 Python 运行 `ops/bk_mcp.py`。配置字段为 `base_url`（受信任的 HTTPS 地址）、`user`、`token` 和 `devices`（授权设备编号数组）。适配器通过标准输入输出工作。

此安装路径覆盖转发接入。上游可选的 hub-bridge P2P 网络需要另行部署。试点的无线 ADB 救援和维护开关需要每台手机与主机单独配对，新实例默认关闭。设备、现网账号、任务历史和证据都属于原实例。

单设备自动更新桥接可在私有持久目录 `/user/phone-pilot/bk-bridge-devices.json` 登记，例如 `{"实际设备域":1}`，其中数值须为这台设备已授权的用户 ID。缺少此文件时默认不登记设备。登记后，服务器在设备上线、转发连接更换或目标桥接文件变化时检查文件及运行状态，并在空闲时补装。正式包发现仅为该设备启用，无需开启全局正式包开关。页面可以关闭。服务器使用镜像内的桥接包，APK 由 BK CLAW 自己的更新机制管理。结束试点时先等在途安装回执确认，再移除对应条目。

对允许群控自动亮屏的无密码试点手机，在持久文件 `/user/phone-pilot/bk-ai-devices.json` 中添加对应设备域，保留已有条目和 `_default`。配置示例如下。

```json
{
  "_default": {"runtime_env": true, "info": "auto"},
  "替换为同一设备域": {"runtime_env": true, "info": "auto", "prepare_unsecured": true}
}
```

设备空闲且收到 AI 任务时，桥接会唤醒手机，必要时打开 BK CLAW，待手机就绪后提交一次任务。关闭该设备的自动亮屏可将其 `prepare_unsecured` 改为 `false`。查看网页或查询任务状态不会触发亮屏。

### 从 0.5.0 升级已有独立部署

在原部署目录替换新版安装包内的部署文件，保留原 `.env`、Compose 项目名、私有配置及数据卷。若曾自定义旧镜像内的 `/opt/bk/devices.json`，先导出并将设备条目合并到 `/user/phone-pilot/bk-ai-devices.json`。若 `.env` 设置了 `BK_IMAGE`，更新到本版镜像标签或发布页的摘要。确认手机空闲后，仅更新应用服务。

```sh
docker compose pull pigeon
docker compose up -d --no-deps --no-build --wait pigeon
```

升级后核对服务健康、设备在线和桥接确认状态。需要回退应用时，重新指定上一版 `BK_IMAGE`，再次执行上述两条命令。

## 检查、重启与迁移

```sh
docker compose ps
docker compose exec pigeon supervisorctl status
docker compose exec pigeon python3 -c "from server.bk_native_gateway_health import check_gateway; print(check_gateway())"
```

正常时应用及数据服务显示健康，网关检查输出 `True`。保留数据的停机使用 `docker compose stop`，重新启动使用 `docker compose up -d --wait`。加 `-v` 的 `down` 命令会删除数据卷。

备份时保存私有 `.env`、PostgreSQL、`user`、RabbitMQ、Redis 数据和已启用的 TLS 卷。恢复已有实例前先在隔离环境还原并核对设备、证书与任务。切换同一批生产设备时，等在途任务完成，在旧实例停止接收工作后制作一致备份，再切换设备配置。新旧实例同时调度同一台手机可能重复执行任务。

## 开发者从源码构建

在完整源码的 `deploy/standalone` 目录生成独立实例的配置后，使用以下命令构建当前代码。构建还需要访问 npm 和 PyPI。后续管理这个源码实例时继续使用同一对 Compose 文件。

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build --wait
```
