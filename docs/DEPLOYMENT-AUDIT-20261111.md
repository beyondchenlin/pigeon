# 服务器与 dev 一致性核对：2026-11-11

结论：本轮检查的 102 个前端构建文件、11 个 BK 二开后端/网关文件与服务器一致。另补齐了 11 份已在线使用、尚未进入正式仓库的兼容代码及部署配置。没有重新部署、重启服务或修改 `main`。

线上使用原始镜像加增量代码，不是从整个 `dev` 重新构建的镜像。因此可以确认下述二开文件一致，不能笼统宣称整个镜像与 Python 源码逐字一致或已验证可复现构建。

## 核对基线

| 项目 | 核对值 |
| --- | --- |
| 仓库 | `https://github.com/beyondchenlin/pigeon.git` |
| 活跃分支 | `dev` |
| 核对前 dev | `6bbf0628a17ff8ae99b4c11fe8ffd2118f3f9d5f` |
| 线上前端 revision | `4bcd6b30839c98735ce350dd4ad68d2eb10dbb41` |
| 前端 release 分支 | `dev` |
| 前端发布包 SHA-256 | `599ab8542134f8011ea9f530935fbb681425ee19801a07c59d2087f81168aad3` |
| PIGEON 容器 | `phone-pilot-pigeon-pigeon-1` |
| 镜像标签 | `firerpa/pigeon:1.0` |
| 实际运行镜像 ID | `sha256:2cf585fcc628a5d1b1212a1cc4bc62fcfbe81002a542c6ba31e1860ad4d2b36f` |
| 受保护的 main 基线 | `e282a5aa9cea9f7ded61cc6a9654f79476119b66` |
| 原里程碑 | `dev-media-recovery-checkpoint-20261111` → `6bbf0628a17ff8ae99b4c11fe8ffd2118f3f9d5f` |

前端 `4bcd6b3` 之后、核对前 `6bbf062` 之前的两次提交仅修改文档，不改变运行代码。本次归档提交也不改变线上行为，所以无需为更新审计记录重建前端。发布版本号与仓库最新文档提交号不同是预期情况。

## 文件检查及差异说明

| 范围 | 方法及结果 |
| --- | --- |
| 前端 | 本地已有 `vue/dist` 与线上 `frontend/current` 逐文件 SHA-256 比较：102 个一致、无缺失、无内容差异；服务器仅多 `release.json` |
| BK 扩展 | `server/bk_metrics.py`、`bk_telemetry.py`、`bk_ice.py`、`bk_frame.py`，以及 6 个 `ops/` 文件、`lua/bk_device_console.lua`，共 11 个：本地、Git blob、线上字节均一致 |
| 服务初始化 | `server/__init__.py` 线上与 Git blob 一致；本地仅 Windows CRLF 换行差异 |
| 服务入口 | 线上 `server/__main__.py` 使用安装器生成的 `/user/phone-pilot/bk-server` 扩展加载钩子；仓库源码构建使用相对导入。两者路径不同，不能作为完全相同的文件处理。现有启动器和安装器均已入库 |
| 鉴权源码 | 线上挂载版与 `executor/auth.py` 只差一处空行；保存现场版本到 `deploy/pilot/auth.single-phone.py` |
| FRP 启动脚本 | 线上 `bk-frps.sh` 与仓库 `frps.sh` 归一化换行后相同 |
| 原始编译模块 | 记录了 6 个 `.so` 的 SHA-256；`docker diff` 未发现这些模块被修改。未进行源码重编译对照，不把哈希记录等同于源码一致性证明 |

文件级校验值、实际镜像身份、现场 Python 依赖版本和明确排除项保存在 [机器可读清单](../deploy/pilot/audit-20261111.json)。该镜像没有 `RepoDigests`，这里只记录实际 image ID；Compose 内的标签本身不是不可变版本锁。未来迁移服务器时需验证镜像来源和身份，不能仅凭同名标签认为相同。

## 补齐的部署资料

新增 [deploy/pilot](../deploy/pilot/README.md)：事件心跳兼容、鉴权挂载版、等待鉴权再启动 FRP 的启动器、单机 Nginx/Supervisor/Compose 配置、Postgres/RabbitMQ 配置和 3 个 Redis 启动脚本，共 11 个现场文件，已再次按 SHA-256 与服务器核对。

其中 `heartbeat_compat.py` 仍是当前设备域的单机兼容逻辑，归档时未改变其作用域。Compose 的资源限制也针对现有试点；不能把这份快照直接视为多设备规模化配置。附带的 `.gitignore` 排除私有环境、数据、证书、密钥、日志与备份，`.gitattributes` 保留 Linux 文件换行。没有提交真实密码、证书、数据库、手机数据或构建包。

## 验证

- 迁入已有的心跳回归用例：`python -B -m unittest discover -s tests -p test_bk_heartbeat_compat.py -v`，8 项通过。
- 归档 Python 源码语法检查通过；现场 `docker-compose config --quiet`、3 个 Redis 脚本的 `sh -n` 检查通过。
- 线上 `nginx -t`、FRP 鉴权依赖 `--check-only` 通过。
- API、事件、FRP、鉴权、OpenResty 进程均为 RUNNING；API 未登录探针返回预期的 401。
- 原前端与媒体代码没有修改，不重复安装依赖或全量构建；之前的测试和真实手机验收结果保留在 [媒体修复记录](./MEDIA-RECONNECT-FIX-20261110.md)。本次核对不意味着此前延迟长尾已经解决。

没有新增 worktree、下载镜像或复制运行环境。保留本轮小体积、任务独占的本地审计记录供回查；运行数据和编译产物不进入 Git。
