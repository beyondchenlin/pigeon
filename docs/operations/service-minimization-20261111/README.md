# 手机附加服务收敛：本次对话归档

日期：2026-11-11。范围为“检查远程控制项目的调试痕迹”这次对话，不包含其他对话的前端、媒体恢复或 BK AI 集成开发。

## 已实施的修改

本次实际修改发生在手机：关闭 FIRERPA 内置 SSH、空闲的手机 cron 和 HTTP 网桥代理开关；内置 ADB、mDNS 继续关闭，并明确写入持久配置。保留 Shizuku、系统调试、非 Root 状态和远控必需服务。具体修改、实施时的进程对照与恢复条件见 [RESULT.md](RESULT.md)。

后续只读核对确认上述六项持久开关和运行开关均为 `false`，转发运行开关仍为 `true`；手机上的恢复记录仍为 `applied`，四份恢复资料共 5,507 字节。该核对只读取配置及已有记录，没有进行画面、点击、断网、重启或平台风控测试。

## 本次对话的其他结论

- Magisk 相关调查未引起安装、Root 或系统改动，继续采用无 Root 远控方案。
- FIRERPA 实际运行版本为 10.9；增强隐匿开关未设置，未发现运行中的 Frida 服务。本次没有开启该开关。它针对特定 Frida 检测，不能作为隐藏 Shizuku 和系统调试的总开关。来源：[官方 FAQ](https://device-farm.com/docs/zh/faq)、[Frida 说明](https://device-farm.com/docs/zh/frida-usage)。
- 查到 Shizuku 的 thedjchi 分支和 Shevery 提供包名隐匿功能，但没有找到与本机现有 FIRERPA 远控组合的明确兼容依据，因此没有替换 Shizuku。来源：[分支发布说明](https://github.com/thedjchi/Shizuku/releases)、[Shevery 隐匿与兼容说明](https://github.com/HmnDev-Tech/shevery/blob/main/docs/stealth-and-stubs.md)。
- Shevery 的 USB 调试隐藏以及 IMD 等工具会实际切换调试设置或相关服务，不符合本次保持调试和远控不变的要求，因此没有启用。来源：[Shevery 功能说明](https://github.com/HmnDev-Tech/shevery/blob/main/docs/lab-and-extras.md)、[IMD 项目](https://github.com/soul-99/SU_IMD)、[Shizuku 官方手册](https://shizuku.rikka.app/zh-hans/guide/setup/)。
- 以上调查不构成“平台无法检测”或“账号不会被风控”的结论。

## 文件和执行边界

| 文件 | 用途 |
| --- | --- |
| `RESULT.md` | 当时的实施结果、回退条件和 Frida 只读复核记录 |
| `scripts/apply_minimal.py` | 已执行过的一次性变更脚本快照，会修改手机配置；仅归档，本次不重复运行 |
| `scripts/inspect_*.py`、`scripts/read_receipt.py` | 当时使用的只读检查脚本快照；本次仅保存，不运行 |
| `scripts/phone_rpc.py.example` | 连接脚本的脱敏示例，实际 SSH 主机、私钥路径、容器名和手机证书路径已替换为占位符 |
| `MANIFEST.json` | 本目录文件的字节数与 SHA-256，注明原样归档、换行归一化或脱敏处理 |

脚本保留当时的 FIRERPA 内部路径及版本假设，不是可直接用于其他手机或升级后环境的通用工具。`apply_minimal.py` 含有修改前置条件和重复实施检查；未来重新使用前仍需重新审查现场状态。不要将本目录接入自动部署、服务启动或定时任务。

没有收录密码、私钥、证书正文、原手机配置、数据库、运行环境、构建包或手机数据。含连接信息的 `properties.before` 及其他恢复文件仍只保留在手机原目录，未下载或同步到 GitHub、服务器。

## GitHub 与服务器归档

本目录作为独立提交保存到 `beyondchenlin/pigeon` 的 `dev` 分支，`main` 不变。只暂存本目录，其他对话的工作不进入本次提交。

服务器副本保存到 `/opt/phone-pilot-20261108/records/service-minimization-20261111/`。其中 `SYNC.json` 在同步时记录确切 Git 提交号、分支和文件校验值，仅保留在服务器；其余文件与该提交逐文件一致。传输后只读取文件并比较校验值，不运行归档脚本，不部署应用，不重启或重载服务，也不连接手机执行操作。

复用现有工作区、Python 和 SSH；无新工作树、依赖或构建。归档不足 100 KiB，不需要临时压缩包。原本地工作记录继续保留，避免删除历史依据。负责人：本任务用户；手机恢复资料复查日期：2026-11-18。本次仅做归档，不执行回退。
