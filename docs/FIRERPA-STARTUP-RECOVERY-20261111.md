# FIRERPA 重复启动超时：历史核对与定向恢复（2026-11-11）

## 结论和证据边界

历史中的“清缓存后恢复”主要是清理已经确认卡死的服务子进程、失效维护连接，以及修正启动路径，不能统称为应用缓存。没有证据支持“每次换 Wi-Fi 都必须重新输入配对端口，否则 FIRERPA 必然启动超时”。

本次查阅了聊天实际消息，而非只看标题摘要：

- 《评估豆包方案合理性 (2)》（`01a1b98b-8ca3-7183-b195-92a1d5f1e618`）：旧 SSH 会话占用端口；恢复 Termux 后清理 FIRERPA 残留进程；App 与手工启动路径不同，统一路径后 App 启停恢复。
- 《评估豆包方案合理性 (4)》（`01a1c3ae-9fb6-7842-b095-f8ea74325b19`）：主进程等待子进程退出；确认 UID、可执行文件、启动时刻、父子关系后只结束卡死子进程。另一次修改原生文件导致完整性校验报 `file broken`，恢复原始文件后修好。继承 `__DNTNEEDRESPAWN__` 的错误启动环境也曾跳过必要初始化，已由干净环境启动修正。
- 同一聊天的媒体故障：父线程阻塞在 Python exec 错误管道的读取端，子进程仍保留同一管道的 CLOEXEC 写入端、线程停在 futex，未完成 exec；只终止该子进程即可恢复原主进程。对应仓库记录见 `MEDIA-RECONNECT-FIX-20261110.md`。
- 《检查远程控制项目的调试痕迹》（`01a1c7b3-3c8e-7301-a35e-18333eb0fdee`）的服务收敛交接继续有效，保留提交 `0a741be` 的归档内容。

配对端口与 ADB 连接端口是两种不同端口。Android 无线 ADB 的连接端口可以变化，配对也不等于持续保持连接；参见 [AOSP 无线 ADB 设计](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/dev/adb_wifi.md)。本机 App 启动逻辑等待服务 PID/进程启动，并不直接检查无线配对端口。非 Root 启动仍需要 Shizuku 等已授权启动条件；不能承诺手机重启或系统撤销授权后完全无需人工操作。

## 已安装的修复

### 1. 只恢复已证明的 exec 前卡死

`ops/bk_lifecycle_monitor.py` 在独立的单线程 Python 进程中运行，通过原有 Bionic `posix_spawn` 启动。监视器不清缓存、不改系统设置、不检测网络后盲目重启，也不向 FIRERPA 主进程发终止信号。

干预同时要求：同 UID、同可执行文件、同命令行、直接父子关系、子进程年龄至少 20 秒、1–2 个线程均阻塞在 futex、父线程正在读取 exec 错误管道、子进程保留对应 CLOEXEC 写入端。两次连续采样一致后再次核对身份，并用 pidfd 锁定该任务；先 TERM，仍符合条件才 KILL。每 10 分钟最多 3 次。缺少权限或无法确认身份就不干预。

正常运行的主进程、普通空闲子进程、已 exec 的任务、AI 任务和用户明确停止的主服务不属于恢复目标。监视器随主服务退出，不自行复活主服务；若已存在由官方 PID 文件确认的替代主进程，则跟随它。

`ops/bk_lifecycle_extension.py` 供下一次正常服务启动加载。本轮直接启动了同一个监视器，没有为加载扩展重启主服务。扩展对原生版本与哈希设门限；版本不符或监视器启动失败时跳过恢复层，避免让可选功能造成新的主服务启动失败。

原生包仍是 FIRERPA 10.9，bundle SHA-256：
`03841b518f9a1308de3af62ae2fddbf4b5c0b918fa4e4aa701c5771a720fd05f`。

### 2. 自动发现已有无线调试连接端口

在本机安装的 `framework.jar` DEX 中确认 `TRANSACTION_getAdbWirelessPort = 10` 后，使用只读 Binder 查询取得实际连接端口。每次启动核对框架哈希，框架变更后停用该查询；不能凭 Android 版本猜事务编号。此机 `service.adb.tls.port` 为空，Termux 的 mDNS 命令也不支持，故没有启用已关闭的 FIRERPA mDNS。

监视器每 15 秒把时间戳和数字端口写入 Termux 私有目录；Termux 私钥始终留在原目录。`ops/bk_termux_tunnel.sh` 采用原有 SSH 身份、主机校验和私有反向端口，删除等待人工输入端口的阻塞提示。新鲜端口变化时只替换自己的 SSH 子进程；调试不可用时等待，不启用调试或改网络。

Termux 原有保持唤醒功能继续使用。远程启动时必须保留 Android 的 `BOOTCLASSPATH`、`DEX2OATBOOTCLASSPATH` 等运行环境；本轮发现省略它们会让 Termux 的 `am` 启动器 abort，已修正并确认 `termux:service-wakelock` 实际持有。不得把缺少环境的 `env -i` 当作有效启动模板。

SSH 日志上限约 20 MiB/份、保留 4 个旧文件；轮转前先让所属 SSH 子进程退出，防止继续向已重命名的文件无限写入。监视器和云端 ADB 维护日志分别为 1 MiB/份、保留 2 个旧文件。

### 3. 云端不再把旧 offline 连接当成在线

断线验收发现 SSH 反向通道重建后，ADB 可能保留旧 `offline` 传输；`adb connect` 返回 `already connected` 不能证明设备在线。旧 STF 的 bridge 与其他平台组件已停用约 41 小时，本轮继续保持停用。

新增 `ops/bk_adb_keepalive.py` 与 `ops/phone-pilot-adb-maintenance.service`，复用已有 ADB Unix socket，每 15 秒核对唯一已有端点 `127.0.0.1:15555`。仅在 offline 时断开这一条旧传输并连接；最终以 `host:devices` 的 `device` 状态为准。健康设备、其他设备和未授权状态不重置。不执行配对、不重启 ADB server、不运行手机命令、不开放新 TCP 监听、不触碰云端 Celery。

安装位置：`/opt/phone-pilot-20261108/ops/bk_adb_keepalive.py`；systemd 单元名 `phone-pilot-adb-maintenance.service`。仅使用已有系统 Python，无新依赖/容器；内存限制 32 MiB，日志在本轮记录目录内。

## 验证及本轮异常的如实记录

- 复用服务器现有容器，23 项相关测试通过、0 失败、0 跳过：包含进程身份、错误管道、pidfd、扩展失败时主服务继续、端口新鲜度及 ADB offline 误报回归。
- 手机上 9 项进程相关测试通过。另用任务独占的模拟故障完整运行监视器，25.53 秒自动结束模拟卡死子进程；模拟父进程和真实 FIRERPA 主进程均未停止。模拟目录和进程已清理。
- 主服务 PID `647`、启动标识 `48466106` 在安装和验证期间保持不变。监视器 PID `11200`，对真实服务的干预次数为 0；这不是声称已观测到一次真实复发并恢复。
- 第一次替换维护助手时 RPC 超时，没有完成收据；随后回读确认脚本已写入、旧助手已退出、新助手未运行。原替换步骤只等旧助手 3 秒，不能把这一轮算作成功。未重放不确定的整套替换；单独恢复助手后改用预先记录阶段、最多等待 18 秒、独立进程完成交接的方式，最终收据为 `verified`。
- 用户确认上述中断前没有切网络，手机已熄屏；不能将那次中断直接归因于换 Wi-Fi。当前屏幕/功耗状态变化和远程恢复命令缺少 Java 环境分别记录，不据此推断所有历史故障。
- 只终止已核实身份的维护 SSH 服务端会话，助手在 **9.02 秒**自动重连，无人工操作。之后进一步发现 ADB 的 offline 状态问题，才增加云端维护服务。
- 后续只读回查确认系统 `adb_wifi_enabled=0`、`adb_enabled=1`，无线连接端口不可用；FIRERPA RPC 仍可正常执行，主进程未变。没有打开无线调试来制造验收通过。因此，**未完成“真实切换 Wi-Fi 后 ADB 恢复在线”的现场验收**，目前只确认 SSH 自动重连、端口发现逻辑及 offline 恢复回归测试。
- 本轮没有整机重启、切网、服务 App 启停或手机休眠长测。下一次真实启动时的扩展自动加载仍需观察；系统挂起可能推迟采样，25.53 秒不是休眠条件下的时限保证。

这是一层针对已证实卡死形态的自动恢复，不是修改上游所有原生 fork 调用路径。若以后出现不同故障，保留事件和版本证据后分类处理，不把所有超时都套用同一清理动作。

## 保留项、回滚和资料

FIRERPA `sshd.enable`、`cron.enable`、`tunnel2.enable`、`adb.enable`、`mdns.enable`、`mdns.meta` 保持 false；原生 SSH/cron 自动启动未恢复，`enhanced-stealth-mode` 仍未设置。保留 Shizuku、系统调试、非 Root、原有远控及 AI/人工输入互斥逻辑。手机 properties 全文件哈希安装前后相同：
`acd46fe1a98e1286a3b761fbe2369e3a23d7fa7cd36c10a2972647dd9655b24b`。

- 原敏感恢复目录 `/data/local/tmp/usr/phone-pilot/service-minimization-20261111/` 原地保留，不进 Git。
- 本轮源码恢复目录 `/data/local/tmp/usr/phone-pilot/lifecycle-recovery-20261111/`，以及 Termux `~/phone-pilot/start.before-lifecycle-20261111.sh`、`start.before-logrotation-20261111.sh`，只保留本轮变更所需源码，不包含配置副本。维护负责人 phone-pilot 维护者，复查日 2026-11-18。
- 手机阶段收据 `/data/local/tmp/usr/phone-pilot/lifecycle-stage-20261111/`；运行状态/有界日志 `/data/local/tmp/usr/phone-pilot/lifecycle/`。
- 服务器记录 `/opt/phone-pilot-20261108/records/firerpa-startup-20261111/`；本机 `E:/qk/phone-pilot-20261108/firerpa-startup-20261111/`。保留诊断脚本和小型收据用于追溯，不收集原始敏感日志。
- 回滚恢复层时只处理新增扩展、已核实身份的监视器及对应源码；不要恢复整份 properties，不执行服务收敛归档脚本。Termux 回滚只恢复其 start.sh 源码并使用完整 Android 环境启动。云端维护层可独立停止/禁用该 systemd 单元，原 ADB server 与远控服务无需重启。

本轮未新增工作树、虚拟环境或下载；复用现有 Python/容器。测试模拟资源已清理，保留上述小型源码/恢复资料和有界日志；不删除其他聊天产物。
