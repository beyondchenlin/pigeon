# 单机真实视频发布验收：2026-11-11

> 公开版本：真实任务、运行和证据标识已替换为匿名代号；原始验收记录保存在维护者的私有归档中。历史计划与实际验收状态以最新里程碑说明为准。

本轮已在一台 vivo V2220A / Android 14 上，分阶段跑通 **MCP 下发下载任务 → 手机下载和导出 → MCP 下发发布任务 → 抖音私密发布 → 平台截图复核**。视频 4 秒、21,315 字节。发布任务耗时 150.515 秒，记录中只有一次“发作品”点击；私密作品列表、详情文案与 4 秒播放时长均有截图。

这是受监督的单机业务样本通过，不代表已经交付结构化 `video_publish` 工具、自动业务验真器或 16/320 台稳定性。本轮没有重建下载器、手机调度器、任务收据库或新建 WSS。

## 实际结果与证据

| 项目 | 实测结果 | 范围限制 |
|---|---|---|
| 素材下载 D0 | 手机 WebDownloads 下载 HTTPS MP4，share_file 导出到 Download/Mika；手机文件大小与 SHA-256 匹配 | 本次是云服务器静态 HTTPS 文件；尚未接入业务对象存储、签名过期或续签 |
| 平台选片 | 原 MediaStore.Downloads 导出已能在这台手机的抖音选择器中选中 | 不推断其他 ROM、平台或大视频同样可用 |
| 统一 MCP | 官方 MCP SDK 2.3.0，真实 SSH stdio 握手、发现与工具调用；沿用 PIGEON 鉴权、设备白名单、任务表及 request_id | 当前只开放单台试点白名单；不是公网 Streamable HTTP 服务 |
| 视频发布 | 抖音账号 520ip，文案 `BK自动化测试-20261111-01`；仅自己可见；一条私密作品可播放 | 未获取作品 ID/链接，未测试公开发布、评论或私信 |
| 结果核验 | 独立查看手机留存的平台截图，确认私密列表、正确文案/测试画面、锁标识和 00:04 时长 | 这是本轮验收人员的核验；普通任务的 `done` 仍不等于业务 `verified` |
| 事件 D1 | 原 MQTT 状态链路可收事件；首次探针收到 status/task/status/device/status，最终保存的独立探针收到 status | 发现原生 report 方法不等于 BK 可安全接入；自定义事件发布、序号和补报尚未验证。试点继续复用原有有界 RPC 查询 |
| 执行画面 D2 | 原 FIRERPA 的 `get_virtual_display_by_id(...).screenshot()` 实际抓到 BK 虚拟屏 607、612；612 显示测试视频发布编辑页，任务继续至 done，未申请触控 | 只是原生截图路径通过；PIGEON 页面仍有主屏/触控耦合，不能宣称观察 UI 已改造完成。暂停后销毁/失效的 609 抓图会失败 |
| 无密码自动解锁 | 控制性熄屏后：interactive=false、keyguard_showing=true、ready=false；调用已部署 prepare_unsecured 后变为 true/false/true | 仅正常系统唤醒和解除无密码锁屏；未设置或输入密码。一次短时熄屏成功不证明 OEM 长时冻结已彻底解决 |

证据目录：[本轮证据](<private-pilot-evidence>)。其中：

- [acceptance.json](<private-pilot-evidence>)：验收范围、任务、素材、耗时、核验方法、截图哈希和未覆盖项。
- [download-evidence.json](<private-pilot-evidence>)：手机导出文件的独立 SHA-256 与下载/导出工具记录。
- [发布前私密设置](<private-pilot-evidence>)、[私密作品列表](<private-pilot-evidence>)、[发布后的详情](<private-pilot-evidence>)、[4 秒播放器](<private-pilot-evidence>)。截图含自有账号信息，不放公共素材目录。
- [事件探针](<private-pilot-evidence>)、[无密码解锁探针](<private-pilot-evidence>)、[服务器与安装回执](<private-pilot-evidence>)。

素材 SHA-256：`03b8b9943e74047ae1a5b3945d7397edbfb61f259de5fe0496eab6ea598dde2b`。合成视频和网页公开，仅含测试卡片；设备截图、运行记录和凭据没有公开。

## 两个仓库实际改动

基线：PIGEON `40089134bb8e928213268ca08e79b5a03f05dfc9`，BK `5b96f6efe8afbb01342bcaf7597fdf108de0f008`；均在现有 dev 工作区，当前为未提交变更。

**BK截套**：[WebStack.kt](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/cap/web/WebStack.kt) 修复实际下载失败。原下载 IO 线程调用 `WebTab.cookieManager()`，间接访问只能在主线程使用的 AndroidX Profile API，出现 `IllegalStateException: Must be called on the UI thread`，下载为零字节。现在在 WebStack 的主线程构造阶段获取 CookieManager，后台每一跳仍读取当前目标 URL 的 Cookie。保留原 WebDownloads、地址/路径保护和 ShareExporter。

APK 已覆盖安装到原包，保留配置。版本名仍为 0.1.37（38），以源码基线和 APK 哈希区分此次修复；不是另发正式版本。安装会话 1911090811 最初等待回调超时，后续查询证实 `finished=true, success=true, INSTALL_SUCCEEDED`，没有重复安装。

APK：112,234,407 字节；SHA-256 `d5ea9c15ea6e7765490db503de5e96ed0644be1f699146a079c88578cbaabd0d`。

**PIGEON**：

- [ops/bk_mcp.py](https://github.com/beyondchenlin/pigeon/blob/dev/ops/bk_mcp.py)、[requirements-mcp.txt](https://github.com/beyondchenlin/pigeon/blob/dev/ops/requirements-mcp.txt)：增加统一 stdio 入口。工具为 list_devices/list_tasks/get_task/submit_task/cancel_task；继续调用已有设备任务 API，不另存业务状态。设备白名单、请求 UUID 校验、不跟随重定向、不重试创建；HTTP 失败返回 MCP 工具错误，不伪装成功。
- [ops/bk_ai_phone.py](https://github.com/beyondchenlin/pigeon/blob/dev/ops/bk_ai_phone.py)：在既有 AI 占用与文件锁内，按设备显式配置进行正常唤醒、dismiss-keyguard、打开 BK 和 readiness 检查。只对新的任务创建生效；查询和重复请求不反复切前台；准备失败不会发出任务 POST。
- [server/bk_ai_transport.py](https://github.com/beyondchenlin/pigeon/blob/dev/server/bk_ai_transport.py)：校验配置开关并传到原手机桥，给准备流程有界超时。原设备鉴权、脚本互斥、取消和 unknown 语义保持。
- MCP 依赖隔离在 `/user/phone-pilot/bk-mcp-deps`，只用于独立 MCP 进程；主 PIGEON API 未安装/加载该 SDK。更新桥与 API 后只重启 API 服务，未重启原生手机或媒体服务。

## 真实任务历史

| 阶段 | PIGEON task_id | 结果 |
|---|---|---|
| 最初下载 | private-record-022 | 模型选了不可联网的沙箱路径，又遇锁屏；已取消，无发布 |
| 明确 Web 下载 | private-record-023 | 真实触发 Cookie/Profile 主线程错误；失败，无发布 |
| 修复后下载 | private-record-024 | done；下载、哈希、导出、平台选片通过，无发布 |
| 首次发布尝试 | private-record-025 | 用户打开目标 App，BK 保护性暂停；完整工具记录停在账号核对。确认取消后才另建下一次请求，没有发布点击 |
| 私密发布成功 | private-record-026 | done；run `private-record-027`，29 步，150.515 秒，一次发布点击，独立截图核对通过 |

第一次成功下载创建时曾返回 unknown，随后查询原 ID 恢复到 running/done；未重放创建。unknown 和等待取消必须保留设备占用的现有设计得到实际验证。

暂停时还发现一个可复现的使用障碍：`ChatViewModel.openConversation()` 在非 parallel 且 busy 时直接返回；手机当前对话排队时，通知/Intent 无法切到远程暂停对话。现有远程 API 没有 resume，steer 只接受 running。后续应增加针对原远程任务、受状态限制的显式继续入口；不能简单删除人工操作保护或自动恢复所有暂停。本轮没有修改这个语义。

## 验证与运行成本

- PIGEON AI API/手机仲裁/传输测试：Linux 现有运行环境 **27/27**。
- MCP SDK 协议、精确参数转发、设备范围/参数拒绝、HTTP 失败不重试：**4/4**；部署后的真实 SSH MCP 工具发现通过。
- WebDownloads：复用既有 JDK、Gradle 缓存和 Docker JRE，Linux **19/19**；Android 离线 assembleDebug 成功。Windows 初跑该文件路径相关测试出现 unsafe_path 环境差异，没有为通过测试放松生产路径保护。
- 两仓 `git diff --check` 通过。总计本轮相关测试 50 项通过，非全仓全量测试。
- 本次成功发布 run 记录输入 1,113,851 token，其中缓存 1,069,440；非缓存输入 44,411，输出 5,856。这里只报告运行记录口径，不是账单，也不外推 320 台费用。
- 手机运行 Agent 与工具操作，使用其已配置的 deepseek-flash 模型服务；这不表示大模型推理全部在手机芯片本地。模型调用配额和费用仍是扩容变量。
- 结束时设备在线、controlling=false、未结束远程任务为 0。后台服务器未持续拉视频，调试期间按需取了有限截图。

## 扩到 16 台的下一批工作

服务端只查到当前用户已分配的 **1 台在线设备**。不能把模拟 16 个任务、复制同一设备 ID 或一次发布成功写成 16 台验收。

1. 在已跑通路径上接入结构化素材/账号/可见范围约束、手机本地 deadline 和已有 stepBudget；把发布提交与结果未知状态写进持久业务收据。普通 goal 的 prompt 约束不能替代这些限制。
2. 将真实 display_id/任务代次传到观察入口，拆开画面与触控建立流程。默认设备列表不持续预览；只有明确接管才能申请控制。保留原 App 手动操作暂停保护，并补远程任务的状态受限继续能力。
3. 试点先用已有有界查询；在 4 台时测实际查询延迟、CPU/内存/连接、取消确认和模型并发。自定义 BK MQTT 事件只有通过固定版本扩展、身份/序号/确认探针后才能接入，不凭方法名判断可用。
4. 对真实云存储做同一文件哈希、过期链接、续签、拒绝重复导出和选错素材检查；不再用“相册最新一条”作为可靠素材关联。
5. 设备到位后按 **1→4→8→16** 增加。每台先通过无发布的下载/选片/状态观察，再用不同业务键做受控单条发布。每档都验证断网恢复、unknown 不重发、接管终态、任务预算和私密后置条件；记录失败原因与资源曲线，不提前承诺固定服务器容量。

本次不能宣称上述工作已经完成。视频号、其他平台、批量评论/私信和跨地点多节点仍按修订方案分开验收。

## 空间、回滚与保留

没有新建 clone、worktree、模型库或完整运行环境。复用 `E:/bk101`、`E:/qk/pigeon`、现有 JDK/SDK/Gradle 缓存和联调 venv；JUnit 临时容器使用 --rm，完成后已回收。没有清空 Temp 或删除其他任务资料。

保留本轮小体积证据/脚本目录，以及本地 APK 和服务器试点 APK，以便复现/后续试点安装。服务器路径 `/user/phone-pilot/publish-pilot-20261111`；源码/配置回退资料位于其 `recovery` 子目录，手机桥回退副本位于原桥旁。负责人：本轮 BK/PIGEON 维护者；复查日期：2026-11-18。没有将依赖、APK、截图或运行数据库加入 Git。

构建前可用 C/D/E：45.37/121.42/50.94 GiB；收尾抽样：45.33/121.42/51.28 GiB。并发系统活动会影响差值，不能把差值称为本任务净清理量；本轮未执行磁盘清理。详细目标与峰值预算见证据目录 space-and-build.md。
