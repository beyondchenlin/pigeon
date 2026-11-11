# Review 2：按实际代码复核 MCP 手机集群二开方案

> 公开版本：真实任务、运行和证据标识已替换为匿名代号；原始验收记录保存在维护者的私有归档中。历史计划与实际验收状态以最新里程碑说明为准。

日期：2026-11-11。审查对象：[二开实施方案](BK-MCP-FLEET-IMPLEMENTATION-PLAN-20261111.md)，原 v1.0 已在本轮修订为 v1.1。

后续实施记录：[单机真实发布验收](BK-MCP-PUBLISH-PILOT-20261111.md)。以下仍保留静态审查时的基线与发现；新证据已证明这台手机的抖音选片、一次私密发布及虚拟屏截图可行，不代表下列批量业务缺口均已修复。

结论：手机独立执行、PIGEON 监控/接管、统一 MCP 与云存储直下载的方向有现有单机链路支撑；原方案对业务入口、验证、桥接兼容、媒体与扩容的描述仍不够贴近代码，不能原样转成开发清单。以下发现均已反映到 v1.1 文档，**不表示运行代码已经修复或新能力已经实现**。

审查基线：

- PIGEON：`PIGEON 工作区`，`40089134bb8e928213268ca08e79b5a03f05dfc9`。
- BK截套：`BK101 工作区`，`5b96f6efe8afbb01342bcaf7597fdf108de0f008`。
- 本轮为源码、现有测试和验收记录的静态复核；没有连接生产设备下发任务，没有重跑 Android/服务器环境测试，没有测量 16/320 台容量。
- 下文 P1 表示必须在真实批量业务前解决的规划缺口；P2 表示需纠正的复用、选型或规模假设，不是对现有通用单机接口的漏洞评级。

## R1 · P1：远程任务没有接入已有小红书业务计划

**代码事实：**

- [RemoteTaskApi.kt:74](https://github.com/beyondchenlin/bk101/blob/dev/core/src/main/kotlin/com/wangzan101/mika/core/remote/RemoteTaskApi.kt#L74) 的 POST 只接受 `goal/conversation/request_id`。
- [RemoteTaskHub.kt:57](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/RemoteTaskHub.kt#L57) 保存收据后调用 `TaskService.startMcp`。
- [TaskService.kt:229](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/TaskService.kt#L229) 的 MCP 分支仅把目标、对话、来源和外部 ID 传入 `begin()`；[startMcp:1028](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/TaskService.kt#L1028) 也没有业务计划参数。
- [TaskRun.kt:512](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/TaskRun.kt#L512) 以 `businessPlanId != null` 决定业务锁和业务分支；[TaskRun.kt:1022](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/TaskRun.kt#L1022) 只在该分支配置业务 gate。

**影响：**普通远程 goal 能操控手机，但不能据此声称它已经使用小红书计划里的账号核验、配额、目标约束和发送账本。单纯在服务端 JSON 增加 `account_ref`、`business_key` 或 `businessPlanId` 不会使这些规则生效。

**修订：**B-01 明确增加经过验证的一次性业务上下文，端到端接入 TaskService/TaskRun 与原发送协调器，关联计划修订号、运行代次和远程任务 ID。保留原 MCP 来源，不伪装成定时 Trigger，不因一次外部请求启动持续计划。视频发布使用其自己的业务上下文。

**实施验证：**故意使用错误账号、过期计划代次、失效候选和重复业务键，确认请求在写动作前被拒绝；查看实际调用的是业务发送协调器，而非仅把限制加入 prompt。

## R2 · P1：存在 verified 字段，不等于主执行链启用了复核

**代码事实：**

- [TaskRun.kt:999](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/TaskRun.kt#L999) 显式传入 `reviewFinalAnswer = false`。
- [AgentLoop.kt:922](https://github.com/beyondchenlin/bk101/blob/dev/core/src/main/kotlin/com/wangzan101/mika/core/agent/AgentLoop.kt#L922) 在该开关关闭时返回 `Succeeded(... verified=false, reviewed=false)`，不调用后面的最终答案 review。
- [RemoteTasks.kt:111](https://github.com/beyondchenlin/bk101/blob/dev/core/src/main/kotlin/com/wangzan101/mika/core/remote/RemoteTasks.kt#L111) 仅在 `reviewed` 时输出 `verified`。普通结果中该字段缺失并不是验证成功，也不等同于已经核对失败。
- [RemoteTaskReceipts.kt:91](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/RemoteTaskReceipts.kt#L91) 的终态保存逻辑会去掉 `sources`，远程收据不是现成的完整证据导出接口。

**影响：**不能把“手机返回 done”作为发布成功的依据。原方案“从模型自检扩展”容易让人误以为当前普通任务已启用完整最终复核。

**修订：**B-05 与结果契约区分未核对、已确认、无法确认；评论/私信优先输出原发送协调器的确认结果；视频增加业务后置条件。无需为了形式上有一个 verified 而全局打开第二次模型审查，模型复核也不能代替平台结果证据。

**实施验证：**验证 `verified` 缺失/false/true 的兼容；审核中不得显示为已公开发布，私信本端可见不得显示为对方已读；证据必须对应同任务、账号和业务对象。

## R3 · P1：v2 不是两端加字段，手机桥接和守卫也要同步

**代码事实：**

- [PIGEON bk_ai.py:137](https://github.com/beyondchenlin/pigeon/blob/dev/server/bk_ai.py#L137) 拒绝除 `goal/request_id/timeout_s` 外的字段；时限为 15–600 秒。
- [bk_ai_phone.py:73](https://github.com/beyondchenlin/pigeon/blob/dev/ops/bk_ai_phone.py#L73) 限定 v1 路由；[创建分支:108](https://github.com/beyondchenlin/pigeon/blob/dev/ops/bk_ai_phone.py#L108) 仅在 `POST /api/v1/tasks` 执行守卫检查和 AI 占用登记。
- [bk_ai_transport.py:48](https://github.com/beyondchenlin/pigeon/blob/dev/server/bk_ai_transport.py#L48) 的 UI 驱动初始化也绑定 v1 创建路径。
- BK HTTP 字段白名单见 R1，当前 API 已有 capabilities，但未声明 v2 业务或素材能力。

**影响：**仅改中央网关和手机领域对象会被中间层拒绝；如果只拓宽 URL 白名单，新的创建路径可能不经过原占用分支。当前 answer/steer/wait 也不能因为手机有 API 就自动出现在统一 MCP 的能力列表。

**修订：**把中央参数/版本 → 传输初始化 → 手机桥接与守卫 → BK API/收据/启动上下文纳入同一个工作包。新旧路由共享同一创建仲裁语义；能力未实现就不暴露工具。

**实施验证：**新旧版本混用、未知字段、重复请求、人工占用、脚本占用、断线与终态回收均经过四层联测。旧客户端仍可用，v2 不能降级成丢失约束的普通 goal。

## R4 · P1：现有业务库不含视频发布，下载/导出也有实际边界

**代码事实：**

- [BusinessState.kt:18](https://github.com/beyondchenlin/bk101/blob/dev/core/src/main/kotlin/com/wangzan101/mika/core/bkjieliu/BusinessState.kt#L18) 的 Interaction 只有评论、评论回复、首条私信和跟进。
- [BusinessState.kt:38](https://github.com/beyondchenlin/bk101/blob/dev/core/src/main/kotlin/com/wangzan101/mika/core/bkjieliu/BusinessState.kt#L38) 的 BusinessPlan 强制 `platform == "xhs"`；不是通用三平台视频发布模型。
- [WebDownloads.kt:43](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/cap/web/WebDownloads.kt#L43) 已有流式网络下载、地址和路径保护，默认单文件 200 MiB、总量 1 GiB、10 分钟时限。
- [ShareExporter.kt:23](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/cap/sandbox/ShareExporter.kt#L23) 导出到 MediaStore.Downloads；[ShareRules.kt:11](https://github.com/beyondchenlin/bk101/blob/dev/core/src/main/kotlin/com/wangzan101/mika/core/cap/sandbox/ShareRules.kt#L11) 限制 100 MiB。文件存在不等于能出现在目标平台的视频选片器里。

**影响：**不能把视频发布写成“现有计划再接一个下载器”就完成，也不能直接放宽所有网页下载/文件共享限额。已有评论代码并不足以证明小红书视频应当优先于其他平台。

**修订：**D0 先验证云文件 → 本地受管文件/URI → 平台选片 → 对应作品结果。复用下载和文件安全原语，保留独立业务素材限额。视频增加业务类型与提交记录；小红书评论/私信复用原分支。16 台先验收一个已证明可行的平台，其他组合逐项增加。

**实施验证：**用实际素材大小/格式，覆盖 URL 过期、版本变更、校验失败、空间不足和选错素材；不靠“相册最新一条”关联视频。具体断点续传设计由失败样本和素材规模决定。

## R5 · P1：按需观察需要修改真实媒体入口，不能只改页面按钮

**代码事实：**

- [DevicesPage.vue:277](https://github.com/beyondchenlin/pigeon/blob/dev/vue/src/pages/DevicesPage.vue#L277) 默认卡片视图且预览未暂停；[LiveDevicePreview.vue:51](https://github.com/beyondchenlin/pigeon/blob/dev/vue/src/components/devices/LiveDevicePreview.vue#L51) 对可见卡片约每 1.8 秒抓图。不是所有设备全天视频流，但也不是默认零画面流量。
- [PhoneControl.vue:96](https://github.com/beyondchenlin/pigeon/blob/dev/vue/src/components/devices/PhoneControl.vue#L96) 在启动画面时同时建立触控；[phone-touch.ts:65](https://github.com/beyondchenlin/pigeon/blob/dev/vue/src/lib/phone-touch.ts#L65) 的 WebSocket 打开后发送 reset，RTC 路径也会 claim。
- [phone-video.ts:137](https://github.com/beyondchenlin/pigeon/blob/dev/vue/src/lib/phone-video.ts#L137) 请求固定 `display: 0`；[bk_frame.py:22](https://github.com/beyondchenlin/pigeon/blob/dev/server/bk_frame.py#L22) 回退截图没有任务显示参数。
- [单机验收](BK-AI-ACCEPTANCE-20261111.md) 已记录 BK 在虚拟屏执行、远控媒体显示主屏，以及触控 reset 触发取消。

**影响：**只加一个“观察”按钮，若仍调用原 PhoneControl 启动链，可能接管/取消 AI；关闭视频流但保留设备卡片抓图，也不能实现预期的低带宽默认模式。

**修订：**P-05 指向 PhoneControl、phone-touch、phone-video、DevicesPage、LiveDevicePreview 等真实入口。先验证当前媒体后端能否捕获 BK 虚拟屏，再承诺执行画面；观察模式不创建输入握手，卡片/手机列表默认预览也单独控制。

**实施验证：**运行中的 AI 任务保持 task ID 和执行状态；观察期间无人工占用和取消请求；虚拟显示重建时旧输入失效。无法捕获时明确显示主屏和任务摘要，不冒充实时执行画面。

## R6 · P2：不应重建已有收据、调度和发送账本

**代码事实：**

- [RemoteTaskReceipts.kt:20](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/RemoteTaskReceipts.kt#L20) 使用 SQLite；包含收件先于启动、claimStart、重启中断、终态和保留窗口。当前 [onUpgrade:30](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/RemoteTaskReceipts.kt#L30) 不支持新迁移。
- [TaskRegistry.kt:25](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/TaskRegistry.kt#L25) 已有 TaskCenter 队列；[ResourceScheduler.kt:12](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/ResourceScheduler.kt#L12) 已管理跨任务 App 占用、等待和其他共享资源。
- [FileRunStateStore.kt:22](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/FileRunStateStore.kt#L22) 原子保存运行状态；[BusinessStateFile.kt:18](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/bkjieliu/BusinessStateFile.kt#L18) 保存加密业务状态。
- [OutreachSendCoordinator.kt:108](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/bkjieliu/OutreachSendCoordinator.kt#L108) 已有输入回读、最终授权、UNKNOWN/CONFIRMED 和不可取消的收尾记账。
- [FeatureFlags.kt:42](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/settings/FeatureFlags.kt#L42) 的任务步数默认不限制；[TaskRun.kt:998](https://github.com/beyondchenlin/bk101/blob/dev/app/src/main/java/com/wangzan101/mika/task/TaskRun.kt#L998) 使用已有 stepBudget，不是始终受 AgentLoop 类默认 60 步限制。

**影响：**重建一套账本/锁会出现多个状态来源、迁移成本和新的死锁风险；忽略已有步数传递又可能漏掉最需要补的远程预算。

**修订：**原收据做有版本迁移，原 TaskCenter/ResourceScheduler 扩展受管任务策略，原业务发送账本关联远程任务。手机本地 deadline 是新增工作；步数上限复用既有机制，模型预算只有覆盖主循环、子任务和旁路调用后才声明支持。

**实施验证：**沿用并扩展现有 RemoteTaskRecoveryDeviceTest、TaskRegistryTest、ResourceSchedulerTest、OutreachSendCoordinatorTest，验证升级后旧记录、重启中断和 UNKNOWN 都保留；不是另写一套只证明新实现自洽的镜像测试。

## R7 · P2：直接决定新建 WSS，跳过了已有设备事件架构

**代码事实：**

- [executor/event.py:49](https://github.com/beyondchenlin/pigeon/blob/dev/executor/event.py#L49) 订阅设备 MQTT 事件，具有设备/会话/序号等元数据与事件分发。
- [executor/auth.py:172](https://github.com/beyondchenlin/pigeon/blob/dev/executor/auth.py#L172) 配置已有 MQTTs 事件与命令链。
- [executor/queue.py:9](https://github.com/beyondchenlin/pigeon/blob/dev/executor/queue.py#L9) 开始定义设备重排和串行队列；这不等于应用业务已经有 exactly-once 保障。
- [RunEventBus.kt:10](https://github.com/beyondchenlin/bk101/blob/dev/core/src/main/kotlin/com/wangzan101/mika/core/run/RunEventBus.kt#L10) 是 BK 的进程内事件分发器。现有 [Outbox.kt:24](https://github.com/beyondchenlin/bk101/blob/dev/core/src/main/kotlin/com/wangzan101/mika/core/run/Outbox.kt#L24) 用于聊天送达，对结果未知的外发明确不自动重试。

**未查明的边界：**本仓库服务端代码不能证明固定版本 FIRERPA 的手机扩展开放了可供 BK 使用的事件发布接口。既不能认定“必能复用 MQTT”，也不能跳过验证直接新建 WSS。

**修订：**增加 D1 探针：优先验证既有事件扩展；不可用时测有界 RPC 查询；必要时才比较批量 HTTPS 与长连接。移除“16 台验收必须新事件通道”的前置条件。遥测可去重补报，业务外发不能采用相同自动重放语义。

**实施验证：**设备身份、独立事件类型、事件序号归属、服务端提交/确认、乱序和缺口恢复；确认不会污染原 PIGEON 脚本事件流。

## R8 · P2：分组和多节点都不能按空白系统设计

**代码事实：**

- [executor/models.py:409](https://github.com/beyondchenlin/pigeon/blob/dev/executor/models.py#L409) 已有 Group，归用户所有；[UserDevice:437](https://github.com/beyondchenlin/pigeon/blob/dev/executor/models.py#L437) 已有设备分配和一个 group_id。
- [server/service.py:1729](https://github.com/beyondchenlin/pigeon/blob/dev/server/service.py#L1729) 已有分组管理入口；当前已有按设备归属的鉴权，尚不能据此认定有完整地点角色权限。
- [executor/event.py:35](https://github.com/beyondchenlin/pigeon/blob/dev/executor/event.py#L35) 把 MQTT client_id 固定为 `event-loop`；复制事件进程并连接同一 broker 不能按普通无状态 API 扩容处理。
- [single-phone-supervisord.conf:22](https://github.com/beyondchenlin/pigeon/blob/dev/deploy/pilot/single-phone-supervisord.conf#L22) 与 [worker:133](https://github.com/beyondchenlin/pigeon/blob/dev/deploy/pilot/single-phone-supervisord.conf#L133) 是明确的单机试点进程配置。

**影响：**先创建一套新分组会重复建设；直接把每个用户的分组当物理 site 又可能造成跨用户地点不一致。仅共享 PostgreSQL/Redis 并增加调度租约，也没有解决事件消费者和设备网关拓扑。

**修订：**16 台单操作员先复用现有分组和设备分配；多人跨地点前补 site/RBAC 的明确映射。多节点由容量或可用性需求触发，逐进程设计 API、调度者、事件消费者、网关、媒体的所有权；320 台数量不强制多节点。

**实施验证：**两名操作员访问同地点与不同地点的隔离；固定 client_id 的消费者行为；采用多调度者后验证旧租约/epoch、失联设备和恢复节点不能重复派发。

## R9 · P2：固定性能值与“上传阶段许可”缺乏业务基线

**证据：**[单机验收记录](BK-AI-ACCEPTANCE-20261111.md) 只覆盖一台 vivo 的设置读取等任务，取消/接管样本均发生在模型工具调用之前；没有真实视频发布和多设备容量数据。当前通用远程任务只有 step/progress 等信息，没有可被调度器依赖的通用“平台视频上传开始/结束”契约。

**影响：**2 秒接受、5 秒状态、95% 成功率、固定 64 活跃任务和 4 核 8 GB/8 核 16 GB 均不能据此确定。独立申请上传许可需要新的阶段状态机，不是配置两个并发数就完成。

**修订：**撤回这些数值决策和首版阶段许可；先按整项视频发布任务限并发，从单任务实测提升。分别记录下载、模型、UI 操作、平台上传和验证耗时，在扩大设备数之前固定业务时限和验收阈值。

**仍保留的硬门槛：**不串账号/素材/收件对象、不重复外发、不把未知当成功、无法确认停止不开放冲突输入；测试发现任一问题即停止扩容。这些正确性要求不依赖猜测服务器规格。

## 修订后的先后顺序

1. **复用现有 v1，做最薄的统一 MCP 适配。**只读任务、查询、取消先跑通；SDK 与 Tornado 的集成方式通过小范围验证确定。
2. **完成 D0/D1/D2。**分别查明素材/平台业务、状态传输、实际执行画面，不先部署新架构再寻找理由。
3. **打通四段 v2 与业务入口。**扩展原收据和调度，加入手机本地时限、业务预算、素材引用与结果证据；桥接守卫同步升级。
4. **验证一个平台的云视频闭环，扩到一个地点 16 台。**已有小红书评论/私信另验远程接入；统计真实结果和故障，不用通用设置任务代替业务验收。
5. **48 → 80 → 160 → 320 台。**上一档数据决定并发与部署变化，其他平台逐项验收；视频号仍在第二阶段。

## 本轮验证与交付边界

- 已核对实际调用点、参数白名单、业务类型、运行开关、存储实现、事件入口和媒体握手，并读取相关已有测试和单机验收记录。
- 已将上述发现写回方案，而非只在本报告里标注。方案中的原固定选型和前置条件已修订。
- 本轮只修改方案并新增本审查记录。文档链接、引用行号与 JSON 示例做静态校验；未执行运行代码测试，因此不声称测试通过或新方案已可部署。
- 没有安装依赖、构建 APK、建立工作树、下载模型或留下测试临时目录。下一次实施仍复用两个既有工作区，并在大空间操作前按本机规则检查磁盘。
