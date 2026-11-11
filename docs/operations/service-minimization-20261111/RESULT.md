# 手机附加服务收敛记录

日期：2026-11-11（北京时间）。用户授权检查当前配置并关闭不影响现有远控的多余服务；明确要求不动 Shizuku、系统调试，不 Root，不做测试。

## 结果

| 项目 | 修改前的实际状态 | 修改后 |
| --- | --- | --- |
| FIRERPA 内置 SSH | enable=true，RUNNING，未发现子会话 | enable=false，STOPPED，autostart=false |
| 手机本地 cron | enable=true，RUNNING；任务目录只有空的 `.cronstamps` | enable=false，STOPPED，autostart=false |
| HTTP 网桥代理 tunnel2 | 运行配置 enable=true | 运行配置和持久配置 enable=false |
| FIRERPA 内置 ADB | enable=false，STOPPED | 保持关闭，并明确持久配置 |
| mDNS 发现 / 元数据广播 | enable=false，meta=false，STOPPED | 保持关闭，并明确持久配置 |
| FRP、画面、触控、音频及其他已有进程 | 正常运行的原进程 | 操作前后 PID、启动时间和 Supervisor 状态一致 |

手机管理命令实际执行身份为 UID 2000。主服务 PID 9209、FRP PID 9884 保持不变。

FIRERPA 内置 SSH 与 Termux 的 SSH 不是同一个服务；此次没有操作 Termux、Shizuku 或系统 ADB。仅关闭手机端空闲 cron，没有修改云端 PIGEON/Celery 的计划任务能力。未启用或更改 Frida 隐匿功能。

## 实施范围

复用已有云端 SSH 和 PIGEON 认证 RPC。未启动临时 ADB、STF，没有更改凭据、网络、认证或远控传输路径。

在 `/data/local/tmp/usr/.local/properties` 末尾追加独立节 `[phone-pilot-minimal-services-20261111]`，原内容逐字节保留。新增六项 false：`sshd.enable`、`cron.enable`、`tunnel2.enable`、`mdns.enable`、`mdns.meta`、`adb.enable`。

同步修改现有 cfgd 的对应运行配置。仅更新 SSH、cron 的 Supervisor autostart 配置，使用指定进程的停止和配置更新接口；没有重载整个 FIRERPA 服务。修改前确认无其他待应用的 Supervisor 配置差异。

检查范围仅为读取配置、任务目录和进程状态，以及修改后的配置回读。**没有进行画面、点击、连通性采样、断网或重启测试，也没有测试平台风控效果。** HTTP 代理的结论是其运行开关和持久开关已关闭，未通过代理请求进行测试。

## 恢复资料

手机保留 `/data/local/tmp/usr/phone-pilot/service-minimization-20261111`，总计 5,507 字节：

- `properties.before`：902 字节，原手机配置，含敏感连接数据，仅保留手机本地。
- `sshd.before`：461 字节，原内置 SSH Supervisor 配置。
- `cron.before`：406 字节，原手机 cron Supervisor 配置。
- `receipt.json`：3,738 字节，修改前后运行值及进程状态。

目录权限 0700，四个文件均为 0600。负责人：本任务用户；复查日期：2026-11-18。保留理由：本次配置变更的定向回退依据，不包含运行环境或编译缓存。

回退须先确认配置没有后续修改：持久文件应仍等于原文件加本次独立节，SSH/cron 配置应仅有本次 autostart 改动。确认后恢复这三份文件及 receipt 中记录的八项原运行值，并只重新加载 SSH/cron 的进程配置、按原状态启动它们。禁止直接覆盖后来修改过的配置；无需重启主服务。此次未执行回退。

## 本地收尾

本目录保留小型只读检查脚本、实施脚本和本记录，便于追溯；`apply_minimal.py` 检测到已实施的配置节时拒绝重复执行。没有新增 worktree、安装依赖或下载运行环境。原子写入的短暂文件由 finally 回收，没有删除原有资料或其他任务产物。

## 增强隐匿模式只读复核（同日后续）

用户要求查清当前版本是否已经自带、是否还需单独开启；保持不 Root、不影响远控、不做测试。

- 实际手机运行模块 `lamda.__version__` 为 **10.9**，命令身份仍为 UID 2000，主服务仍为 PID 9209。
- 持久配置中 `enhanced-stealth-mode` 未设置；运行配置中没有名称包含 stealth 的配置项。不能把“版本包含改进”写成“增强开关已经打开”。
- `frida.version` 未设置，没有显式选择旧版 17.5.2。安装目录同时包含默认 `frida-server` 和备用 `frida-server-17.5.2`。
- 配套 Frida Python 包元数据为 17.17.0、frida-tools 为 14.10.4。这是 Python 包版本，不冒充已启动服务端二进制的版本结果；本轮没有执行该二进制。
- Supervisor 中没有 Frida 服务进程；shell 可见进程中也没有发现 Frida。`svc.frida.enable=true` 是默认配置值，不代表该特权服务已成功初始化或运行。
- SSH、手机 cron、内置 ADB、mDNS 仍为 STOPPED；转发及画面触控相关原进程保持运行。没有对画面或输入做测试。

[官方 FAQ](https://device-farm.com/docs/zh/faq) 明确表示：增强模式针对部分应用的 app-zygote Frida 检测；10.x 默认 Frida 已处理该问题，通常无需额外设置；手动选择 17.5.2 时才可能需要。开关的已知副作用是 Frida spawn 相关能力不可用。[官方 Frida 说明](https://device-farm.com/docs/zh/frida-usage) 描述了内置隐藏补丁。

结论：当前无 Root、Frida 未运行的远控方案不需要单独开启这个 Frida 专用兼容开关。维持未设置状态。本轮只读取元数据、配置和进程列表，没有改手机配置、启动 Frida、注入应用、重载或重启服务，也没有做检测或风控测试。仅新增本地只读检查脚本 `inspect_frida_state.py` 并追加此记录，无新工作树或依赖。
