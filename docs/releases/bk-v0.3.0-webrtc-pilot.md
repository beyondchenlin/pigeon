# BK群控 · WebRTC 真机试用里程碑

远控页面已接入 WebRTC，修正本机域名走代理的问题，并加入鉴权 TURN、视频回退和独立触控重连。品牌、布局、操作入口保持不变。

本次为 `dev` 试用里程碑，`main` 不变。线上运行代码 `d349972`；Tag 同时包含最终验收记录、统计数据和面向账号日常操作、发布、私信业务的下一步方案。

## 已验证

- 同一手机、移动网络、540×1194、15 fps 请求：两种传输各 30 次点击，回显中位数从 1151 ms 降至 619 ms，P95 从 1962 ms 降至 873 ms。
- 两组连续动画均约 15 fps；20 秒观察中最长到帧间隔由 1245 ms 降至 118 ms。有效 WebRTC 样本为 UDP 直连，不能套用到所有 Wi-Fi / TURN 网络。
- RTC 故障时保留 / 恢复 H.264；恢复 RTC 后再切换，触控独立重连。
- 10 项专项测试、类型检查、生产构建、后端鉴权 / ICE / telemetry 检查及单手机交互验收通过。

## 试用范围

当前速度足以开始日常页面操作的小批量人工试用。AI 发布 / 私信工作流、十台并发与长期稳定性尚未验收；历史 P95 350 ms 目标未达到。先收集真实手机反馈，优先完善动作确认、状态读取、任务去重和异常恢复，再依据控制链路测量决定是否迁移国内节点。

- [完整验收记录](https://github.com/beyondchenlin/pigeon/blob/bk-v0.3.0-webrtc-pilot/docs/WEBRTC-ACCEPTANCE-20261110.md)
- [下一步方案与真机记录表](https://github.com/beyondchenlin/pigeon/blob/bk-v0.3.0-webrtc-pilot/docs/PILOT-PLAN-20261110.md)
- [机器可读对照结果](https://github.com/beyondchenlin/pigeon/blob/bk-v0.3.0-webrtc-pilot/docs/benchmarks/webrtc-20261110.json)

回退：当前页加 `?transport=ws` 可单独关闭 WebRTC；旧界面 Tag `bk-v0.2.0-control-ui` 与服务器回滚发布保留。此次里程碑仅新增说明，不重新部署运行时代码。
