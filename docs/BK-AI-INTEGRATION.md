# PIGEON → BK截套任务 API

PIGEON 负责鉴权、设备选择、任务收据、进度与取消；BK截套使用手机已经配置的模型运行原有 `TaskService`。没有增加服务器模型，没有自动输入聊天框，也没有修改网页界面。首版部署与验收范围仅一台手机。真实完成、取消、原生触控接管及限制见 [2026-11-11 验收记录](BK-AI-ACCEPTANCE-20261111.md)。

## 接口

沿用 PIGEON 登录 Cookie 或 `X-User` / `X-Auth`。每次查询、提交和取消都重新核对设备归属。POST 拒绝浏览器 Origin，请使用受信任的 API 客户端；不要把凭据写进脚本、源码或命令历史。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/api/v1/device/{domain}/ai/tasks` | 提交一次，返回 PIGEON/BK 共用 task_id |
| GET | `/api/v1/device/{domain}/ai/tasks` | 最近 50 个收据 |
| GET | `/api/v1/device/{domain}/ai/tasks/{task_id}` | 刷新手机状态并返回持久收据 |
| POST | `/api/v1/device/{domain}/ai/tasks/{task_id}/cancel` | 请求停止，继续查询直到终态 |
| POST | `/api/v1/device/{domain}/ai/tasks/{task_id}/takeover` | 同样请求停止，终态确认后人工接管 |

提交正文：

```json
{
  "request_id": "6bac1200-f099-419c-afb9-37c42691e0ab",
  "goal": "打开系统设置，只查看并报告当前电量、Android 版本；不要修改任何设置，完成后停止。",
  "timeout_s": 180
}
```

每次新任务生成一个小写规范 UUID。重试同一请求必须保留原 request_id、goal、timeout_s。相同 ID 直接返回数据库收据，不再次提交；同 ID 不同内容返回 409。跨设备映射由 UUIDv5(domain, request_id) 分开，不在业务代码写死设备。取消和接管正文为 `{}`。

返回沿用 PIGEON 的 `{status: 0, data: ...}` 外壳，任务状态位于 `data.status`。`result` 为最近一次手机收据，包含 step/progress 或最终 answer；`history` 保存最多 100 次状态/错误变化。`cancel_requested=true` 或 HTTP 202 均不代表已经停止。只有手机的 `done`、`failed`、`cancelled` 才释放已提交任务的设备占用。

## 传输与凭据

本次采用已有 FIRERPA 双向 TLS RPC，在手机 shell 进程中访问 BK 回环 HTTP API。没有部署新的公网 BK 网关、开放手机入站端口或恢复 USB/Wi-Fi。BK 源码称此回环模式为 USB 模式，但这里的网络传输实际走现有远程 RPC。

首次启用通过 BK 已有的 shell 权限服务动作 `com.wangzan101.mika.mcp.USB_ON`；普通 App 无法调用这个受系统权限保护的组件。不开 LAN 监听。回环访问文件仅由手机桥接进程消费，访问令牌和模型密钥不发送到 PIGEON、不写入任务记录、不输出到日志。关闭手机电脑访问或手机重启后需要重新明确启用，不自动绕过用户关闭操作。

部署映射文件默认 `/user/phone-pilot/bk-ai-devices.json`，可用 `BK_AI_DEVICES_FILE` 指定其他位置。键为设备 domain，值包含 `python`、`library`、`bridge`、`state_dir`、`info` 五个手机绝对路径。映射仅保存路径，不保存任何密钥。

桥接文件 `ops/bk_ai_phone.py` 部署到 FIRERPA 用户 modules，`ops/bk_ai_guard_extension.py` 部署到其 extension 目录。输入扩展只支持已校验的 FIRERPA 10.9 原生模块；未加载、原生版本不匹配或进程已退出时，桥接拒绝创建任务。启用扩展需一次受控服务加载，并核实远控恢复后才能提交任务。

在本次 FIRERPA 10.9 / vivo Android 14 实机上，仅使用 shell RPC 时，启动监听器会暂时挂起 BK 无障碍服务；一次只读 `device_info()` 初始化 UI 驱动后，原有 BK 服务恢复绑定。传输层在创建任务前执行该只读初始化，失败时不会继续发创建请求。不关闭 BK 权限，不替换 FIRERPA 原生二进制。正常重连已有无障碍服务需尊重用户对系统设置的授权。

## 持久化、互斥与故障

- `bk_ai_task` 使用既有 PostgreSQL，引用 PIGEON 的 device/user。收据在向手机发送之前提交到数据库。进程重建只查询，绝不恢复创建动作。
- 手机 request_id 去重目前有 30 天/最近 1000 条已终结收据的保留边界；PIGEON 不自动删除自己的收据，因此不会因手机忘记旧 ID 自动重放。
- `unknown` 表示结果未知，保留设备占用。查询超时、401、404、410、5xx 不等于任务未执行。仅创建请求的明确参数/权限/占用拒绝记作 `rejected`；修正后需使用新 request_id。
- 同设备 AI 创建与原脚本调度在同一个 device 数据库行锁下仲裁。已有待执行/运行脚本、人工控制或未决 AI 会拒绝新 AI；未决 AI 阻止新脚本调度，不影响其他设备。
- 手机输入扩展覆盖原生触控（含 RTC 经本地触控 socket）、键盘命令及调试 socket。人工输入先请求取消，最多等待 10 秒确认终态；无法确认则拒绝输入。成功接管后控制 socket 关闭才释放人工占用。查看状态、显示列表和剪贴板读取不会触发接管。
- 人工占用记录进程启动标识、socket FD 和 inode。原生关闭回调没有清理占用时，下次仲裁核对实际连接已消失才清除，不能按任意超时放行。等待取消期间连接关闭的旧输入会丢弃。触控 reset 也申请人工占用，因此打开带触控握手的远控页可能触发接管。
- 断线不排队，不切断唯一通道测试故障。测试中的断线/超时是适配层模拟，不能作为真实 5G 断网恢复验收。
- timeout_s 为 15–600 秒的 PIGEON 监督时限，到期请求取消。服务器停机或通道失联时不保证手机准时停止；这不是手机端强制执行的运行配额。

## 部署与验证

源码运行入口 `server/__main__.py` 加载扩展，Celery worker 初始化信号加载脚本互斥。现有编译镜像使用 `ops/run_bk_server.py`、`ops/run_bk_worker.py` 和 `deploy/pilot/single-phone-supervisord.conf` 的持久启动器。增量部署需保留已在线的遥测/媒体扩展，不能整体替换运行目录。现场旧 supervisor 尚未重读命令时，可在当前 `executor/celery.py` 加入同一 worker 初始化钩子，只重启 API 和 worker；不要为了刷新一项命令重启整个 service 组。

测试复用已有 Linux PIGEON 容器，在任务独占的临时 SQLite 数据库验证 API。`tests/test_bk_ai_api.py` 需要运行时依赖与环境；`tests/test_bk_ai_phone.py` 的文件锁测试需 POSIX。测试不会向真实手机发任务、清空数据库或发送消息。Windows 本地会明确跳过这些环境相关测试。

后台轮询必须挂到实际的 `BaseAsyncIOLoop.start`。只修改基类 `IOLoop.start` 不会覆盖该运行时，可能出现手动查询有效、自动进度和到期取消不工作的情况；测试会在服务器新建的真实 uvloop 上观察周期回调。

官方启动器会自行 `setsid()`，必须使用普通子进程启动；独立维护助手可自行脱离会话，但不能让启动器子进程先成为会话首进程。`launch_official()` 仅负责正确启动，不自动杀进程或重试不确定的启动。旧服务未退出时应先保留诊断证据，再处理明确归属的残留进程。

未涵盖：BK businessPlanId、账号校验/配额、全部业务计划流程、10–100 台规模、跨网关多设备路由、手机本地发起任务与任意外部自动化工具的全面仲裁。BK 可能在虚拟显示执行，PIGEON 当前媒体默认显示主屏，必须按任务结果和实际显示信息验收。
