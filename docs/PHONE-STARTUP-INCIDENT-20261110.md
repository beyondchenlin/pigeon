# vivo 启动超时与离线排查：2026-11-10

## 已确认的原因

不能把“App 启动超时”“设备心跳离线”“远控请求超时”视为同一个问题。

1. **App 与手动启动使用的运行目录不一致。** 已有两份相同版本的程序：手动恢复使用 `/data/local/tmp/CnyL76/bin/launch.sh`，App 使用 `/data/local/tmp/CnyL76/server/bin/launch.sh`。从现有 APK 的 `NativeServicePaths` / `NativeServiceManager` 可确认，App 设置 `USRDIR=/data/local/tmp/CnyL76/usr`，检查其中的 `lamda.pid`，等待最多 60 秒；手动运行的真实 PID、设备配置和数据库在 `/data/local/tmp/usr`。修复前前者没有 PID，后者记录了实际存活的主进程；App 里的配置也不是实际运行的 `[pigeon]` 配置。之前只恢复进程，没有统一目录，留下了状态判断和启动管理问题。
2. **排障 SSH 隧道出现过半开连接。** 旧会话两小时以上没有收到有效数据，却仍占用本机回环上的转发端口。只停止已确认属于 `phone-pilot` 的旧会话后，新连接恢复。服务器原来没有启用应用层 ClientAlive 检测。
3. **转发连接确实发生反复重连。** 同一时段手机本机 gRPC 连续响应约 34–155 ms，云端请求却可超过 5 秒。FRP 日志有 `client exit success`、`control is closed`、`failed to get work connection`，并重新分配转发端口。已定位到转发链路异常，尚未证明根因是运营商线路、TCP 复用阻塞或其他因素；不能称为“香港服务器性能不足”，也不能据此要求手机 Root。

原离线记录：北京时间 09:08:26 后心跳停止。服务器容器正常、没有 OOM 或重启，Shizuku 进程仍在，手机也没有近期重启。初次恢复前存在残留的 FIRERPA 进程；清理后原启动器正常启动。由于当时没有有效运行日志，不能把该次心跳停止的最底层触发原因写成已证明。

## 本次实际修复

- 保留实际工作数据目录 `/data/local/tmp/usr`，将 App 的 `CnyL76/usr` 统一为指向该目录的链接。原 App 配置保存在 `CnyL76/usr.before-path-fix-20261110`；任务用证书和恢复资料从原 `usr/phone-pilot` 移入实际目录，其原访问路径仍可用。
- 修复前后核对实际 properties、证书、原启动器备份的 SHA-256，内容一致。没有复制数据库、删除 App 数据或修改手机账号。
- 在 App 中实际点击停止，再点击启动。随后主进程由 **App 的 `server/bin/python3.12`** 启动，两种 PID 路径返回同一进程，App 显示“运行中”，原来空白的 `server.log` 开始记录完整启动。远控页面原样保留。
- SSH 配置仅针对 `Match User phone-pilot` 增加 `ClientAliveInterval 30` / `ClientAliveCountMax 3`；`sshd -t` 通过，root 用户的有效配置逐字节一致，采用 reload。
- 恢复期间使用 UID 2000 Android shell，未 Root，未启动 STF 应用。

## 仍需完成的稳定性工作

1. 健康判断分三层：手机本地 RPC、云端心跳、云端远控接口。心跳存在不等于可控；云端超时且本地健康时，禁止把重启整个手机服务作为默认动作。
2. 针对 FRP 断线前后采集有时限、脱敏的错误与连接统计，确认实际客户端/服务端版本及生效配置。按官方能力对照 TCP 复用与 QUIC，逐项验证并保留回退，不能盲目延长超时掩盖断线。
3. 转发恢复成功后才允许任务继续，必要时只恢复转发连接；连续失败采用有限重试与退避，避免多台手机同时反复重启。
4. 验收至少包括 App 停启、短暂断网恢复、锁屏/解锁后的状态区分、持续在线和第二台真机。未完成的长时间与多机测试不得写成通过。

国内就近服务器可作为后续链路对照，但它不会修复本次 App 目录不一致。先完成以上稳定性工作，再用同条件结果决定是否迁移。

参考：[FIRERPA 配置](https://device-farm.com/docs/en/properties)、[FIRERPA 日志](https://device-farm.com/docs/en/service-logs)、[FRP 传输配置](https://gofrp.org/en/docs/reference/client-configures/)、[OpenSSH ClientAliveInterval](https://man.openbsd.org/sshd_config#ClientAliveInterval)。官方说明默认不写日志，指定 logfile 后追加写入；不能把没有日志等同于没有异常。原生 App 日志不进入 Git，后续诊断应使用限定时间与容量的采集，而不是长期无上限保留。

## 保全与分支

本次是手机部署路径、专用 SSH 配置修复及 `dev` 文档记录。没有改动网页布局、没有更新 main、没有把本次排障冒充已验收的新里程碑。

- 手机旧 App 配置备份：`/data/local/tmp/CnyL76/usr.before-path-fix-20261110`，用于恢复目录改动。
- SSH 备份：`/opt/phone-pilot-20261108/ops/ssh-keepalive-20261110/sshd_config.before`。
- 本地小型排障资料：`phone-pilot-20261108/browser/offline-20261110-0938`。截图可能包含 App 显示的连接信息，私下保留，不提交或公开。
- 以上恢复资料负责人为本任务用户，复查日期 2026-11-17。未创建新 worktree 或依赖环境；没有清理其他任务的数据或旧验收证据。
