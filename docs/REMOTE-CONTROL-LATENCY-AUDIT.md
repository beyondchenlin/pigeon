# 远控传屏调查与优化方案

调查时间：2026-11-09 夜间至 2026-11-10 凌晨（北京时间）。界面维持用户已确认版本；本轮不修改线上传输协议、手机持久配置或系统代理设置。

## 已保存的开发里程碑

- 仓库：<https://github.com/beyondchenlin/pigeon>。
- 已部署界面基线：`64cfe914cafd2c394df4a1014b746888c83af2e4`，位于 `dev`，此前已完整推送，工作区干净，无需制造额外合并提交。
- 注解 Tag / GitHub 预发布里程碑：[`bk-v0.2.0-control-ui`](https://github.com/beyondchenlin/pigeon/releases/tag/bk-v0.2.0-control-ui)。Tag 固定上述界面基线，后续性能工作继续在 `dev`。
- `main` 保持 `e282a5aa9cea9f7ded61cc6a9654f79476119b66`，没有合入二开代码。

## 当前正式页面并未使用 WebRTC

实际入口是 `/d/<device-id>/`，由 `lua/bk_device_console.lua` 提供 BK 前端。`PhoneControl.vue` 当前创建以下连接：

- 视频：`/ws/screen/25@15/live?type=h264&backend=1&scale=0.5&display=0`，请求 System 编码后端、质量 25、15 fps、半分辨率。
- 触控：`/ws/screen/touch`，独立 WebSocket。
- 状态和导航命令：`/ws/command`，独立 WebSocket，状态轮询间隔 3 秒。
- H.264 解码：手机提供的 `decoder.js` → Web Worker 软件解码 → JavaScript YUV 转 RGBA → 2D Canvas `putImageData`。
- 失败回退 MJPEG，8 fps、半分辨率。当前 BK 组件没有 `RTCPeerConnection`、WebRTC 信令或原生视频轨道播放逻辑；浏览器探针也记录到 RTC 连接创建数为 0。

```mermaid
flowchart LR
  P[手机屏幕 / FIRERPA System 编码] --> F[手机 FRP 客户端]
  F -->|TCP 17000| S[香港服务器 FRPS]
  S --> G[PIGEON 鉴权网关 / 内部 18000]
  G --> N[域名 Nginx / 443 或 19000]
  N --> V[本次电脑代理路径]
  V -->|WSS H.264| W[浏览器 Worker 软件解码]
  W --> C[RGBA / Canvas]
```

服务器核对到网关目标是 FRPS 分配的本机转发端口；域名反代与 PIGEON 网关均已关闭 HTTP 代理缓冲，并支持 WebSocket Upgrade。本机反代 TCP RTT 为毫秒以下至几毫秒；没有依据把 Nginx 页面缓存认定为当前主因。MQTT 用于接入 / 状态体系，并非本页面的视频传输通道。当前画面不依赖本机 USB ADB 或 STF。

## 证据与判断

| 检查 | 本次结果 | 能说明什么 |
| --- | --- | --- |
| 实际浏览器路由 | CDP 报告域名请求连接 `127.0.0.1:7890`；系统绕过名单含服务器 IP，但不含业务域名 | 换成域名后，当前电脑确实重新经过代理 |
| 同一微小 HTTP 请求，3 轮交替取样 | 直连首字节 337–380 ms；显式代理 1613–1867 ms；两组均返回相同 404 响应 | 确认代理路径额外等待；这是包含建连的 HTTP 时间，不是点击延迟，也不是吞吐测试 |
| 浏览器→手机 `status` 请求 6 次 | 566–890 ms，中位数约 590 ms | 经过当前网关和代理的应用层请求响应时间 |
| 服务器→手机，同类请求 6 次 | 135–294 ms，中位数约 141 ms；首个样本较慢 | 手机端与云端路径也有开销，但明显小于浏览器整条路径；两组非同步，不能直接相减当作精确分段延迟 |
| 现有 BK 页面设置列表滚动窗口 | 19.04 秒，70 次视频消息、70 次绘制，约 3.68 FPS，0.346 Mbps；收包间隔中位数 68.6 ms、P95 1219.6 ms、最大 2025.5 ms | 存在连续到帧和明显停顿交替；不是仅静止桌面低帧率 |
| Worker 探针总观察窗口 | 405 帧，接收后到 RGBA 返回中位数 6.2 ms、P95 18.1 ms、最大 44 ms；滚动窗口 Canvas 复制 P95 0.6 ms | 当前电脑解码并非秒级停顿的首要证据；405 帧含静止及运动，不能称为独立滚动解码基准 |
| 队列与恢复 | 观察期出现 3 个 Worker 生命周期，最高待处理消息 8 个 | 存在重连 / 突发到包风险；探针未记录恢复原因，不能断言所有重连都由队列触发，或都发生在滚动窗口 |
| 云端资源 | 系统可用内存约 215 MiB，PIGEON 约 531/768 MiB；swap 使用 0；容器 OOM 计数 0；另取 5 秒无新增 CPU 限流 | 余量小，需要容量管理，但本次没有证据证明 OOM / 持续 CPU 饱和导致当前卡顿 |

这些是当前一台手机、当前网络的有限采样，不是十台容量、真实点击端到端延迟或长期稳定性验收。采样通过固定的设置列表滚动产生运动，结束后回到桌面；未修改手机设置，未采集或保存视频内容。

## 已验证官方 WebRTC 路线可接入，但可靠性尚未通过

已读取这台手机 FIRERPA 10.9 的实际原生 `app.js?v=d0f0d30ad`，与官方文档交叉核对。它实现的是 **WebRTC 视频轨道**，不是把 H.264 塞进可靠 DataChannel：

1. 使用 `/ws/webrtc/signaling` 交换 offer / answer / ICE candidate。
2. `RTCPeerConnection.ontrack` 接收 `MediaStream`，交给浏览器 video 播放。
3. 只有首个 WebRTC 视频帧可绘制后，才关闭 WebSocket 视频连接；媒体连接失败时重新使用 WebSocket。
4. 原生触控和命令仍是 WebSocket。因此只接入 WebRTC 视频，不能宣称控制指令也实现 P2P。

通过原生页临时 URL 参数 `native=1&webrtc=1&audio=0&share_clipboard=0&h264=1&fps=15&quality=25&scale=0.5` 做功能验证，没有保存到手机设置：

- 首次观察成功连接，`connectionState=connected`、`iceConnectionState=connected`、`_rtcSwitchDone=true`。
- 被选中的两端 candidate 均为 `srflx / udp`，没有使用 TURN relay；视频编码 H.264，实际画面 540×1194。
- 该时刻媒体路径 RTT 为 24 ms，已累计解码 94 帧，统计丢帧 / 丢包均为 0。这主要是静止桌面样本，**不代表运动 15/30 FPS，也不代表点击只需 24 ms**。
- 后续观察到 `_rtc` 被清理、`_rtcSwitchDone=false`、WebSocket 恢复。重新加载后也未在限定等待内完成升级，稍后仍回到 WebSocket。计划的运动 WebRTC 对照因此未获得有效结果，不发布虚假的优劣倍数。
- 当前只配置 `stun:stun.device-farm.com`，没有 TURN。尚未拿到失败时的 ICE 事件序列，不能把回退直接归因于某种 NAT、防火墙或手机故障。

结论：复用官方现有媒体通道是合理路线，已经验证能传来真视频；仍需补齐连接超时、重连、TURN 和可观测性。仅打开开关不足以满足稳定远控。

## 优化顺序

### 1. 先消除域名代理绕路，建立可比较的基线

把业务域名加入当前 VPN / 系统代理的直连例外，并验证浏览器新建 HTTP、WSS 连接均走预期路径。保留其他代理规则，先保存可恢复配置；不能仅修改客户端配置文件就声称生效，已有 WebSocket 也需重新建立。

用同一浏览器、同一手机、同一参数重做设置滚动和状态往返对照，避免只凭网页打开速度判断远控。此项不涉及换服务器，也不需要改现有界面。

### 2. 在 BK 远控组件接入官方 WebRTC 视频轨道

- 在 `dev` 新增独立传输适配层，保留现有布局、属性面板和操作按钮。
- 复用真实信令协议、现有认证及设备授权；HTTPS / WSS 信令继续经当前网关。
- 主路径：WebRTC 视频 P2P；首帧可用后才切换；失败有界回退 H.264 WebSocket，再回退 MJPEG。
- 每代连接有独立生命周期，清理 RTC、Worker、WebSocket、计时器和待处理帧；旧连接回调不得影响新连接。画面重连与触控连接恢复分离。
- 记录实际连接模式、候选类型、媒体 RTT、丢包、解码 / 缓冲时间和重连原因；不把媒体 RTT显示为“点击延迟”。
- 为跨网络失败配置受认证的 TURN，优先 UDP，可按网络需要回退 TCP/TLS。短期凭据由服务端发放，不能在前端写永久 TURN 密码。先验证当前手机服务是否接受所需凭据形式，再决定部署配置。
- 原生视频轨道使用浏览器媒体解码。若保留的 WS 软件解码以后成为多卡预览瓶颈，再评估 WebCodecs / WebGL；现在不优先重写整套解码器。

### 3. 单独优化控制链路与恢复策略

当前 touch / command 仍经云端。先确保域名直连，测量输入到响应；按浏览器帧节奏合并多余 move 事件，保留 down / up 顺序，控制旧指令排队。不能随意丢弃任意 H.264 P 帧，视频队列恢复需考虑关键帧和解码依赖。

现有 `pending > 8` 会重启画面和触控，初次恢复至少等待 3 秒；15 秒未绘制也会触发恢复。应记录具体原因并调整有界恢复，防止短时突发到包或正常静止被放大成明显停顿。卡片预览保持低资源预算，详情页避免与同设备卡片重复拉高清流。

### 4. 最后决定服务器地区与容量

当前不建议仅凭“香港”二字先迁移。已成功的 UDP P2P 媒体无需绕经香港，但控制、信令、WS 回退仍经过云端。国内就近节点对这些路径以及 TURN 中继可能有帮助，必须用公司 / 老家实际网络比较 RTT、抖动、丢包和带宽后决定。

当前内存余量不适合直接扩成十台并叠加 TURN。进入多机阶段再做容量规划；先区分每台新增连接、媒体中继带宽、浏览器解码负载与消息服务固定开销。暂不并行重启 STF，不通过增配掩盖路由或协议问题。

## 下一阶段验收与回退

按“直连修正 → 单机 WebRTC → 异常恢复 → 多卡”的顺序，每一步单独提交至 `dev`，通过后部署，`main` 不动。上线保持当前 UI，提供 WS 回退开关，并保留本 Tag 对应前端恢复路径。

单机首轮拟定目标（待实施验证，不是已达到的承诺）：

- 同一设置滚动、15 fps 请求下，连续 60 秒绘制均值至少 12 fps，正常网络无超过 1 秒的非预期黑屏 / 冻结；静止画面不纳入运动 FPS 判定。
- 用带序号的输入与可识别画面变化测量点击→可见响应，统计至少 30 次；初步目标 P95 不超过 350 ms，不能用网络 RTT 替代。
- 30 分钟远控期间保留有界统计，验证实际媒体路径；分别覆盖 UDP 可用和不可用时的 TURN / WS 回退。
- 锁屏 / 解锁、页面隐藏 / 恢复、视频断线、服务重连后可恢复；触控不能残留按下状态；重连不建立多个重复视频流。
- 对比优化前后的服务器 CPU / 内存、单流码率、浏览器帧率、输入延迟与重连次数，不直接上调到 60 fps。

## 依据

- [FIRERPA 官方远控文档](https://device-farm.com/docs/en/remote-desktop)：默认 WebSocket；WebRTC、STUN/TURN、System 编码及并发观看说明。
- [FIRERPA 官方仓库](https://github.com/firerpa/lamda)：远控与嵌入能力；另核对了已安装手机实际提供的原生前端实现。
- [WebRTC 官方 TURN 说明](https://webrtc.org/getting-started/turn-server)：直连不总是可行，需要中继兜底。
- [W3C WebRTC 统计定义](https://www.w3.org/TR/webrtc-stats/)：candidate-pair RTT、累计解码时间及 jitter buffer 指标不能混为端到端延迟。
- [Chrome WebCodecs 文档](https://developer.chrome.com/docs/web-platform/best-practices/webcodecs)：可用浏览器已有编解码能力，硬件加速需检查实际支持。
- [FRP 官方网络配置](https://gofrp.org/en/docs/features/common/network/network/)：支持多路复用、KCP / QUIC；这是需要两端兼容的备选项，本轮未改手机 FRP 配置。

本机原始统计、有限脚本及最小截图保留在任务证据目录 `E:/qk/phone-pilot-20261108/browser/bk-latency-audit-20261109/`，不提交依赖、构建物、第三方原生 JS 副本、登录态、令牌或视频到 Git。本轮复用原工作区和环境，未创建 clone / worktree，未重新构建。开始 C 盘余量约 18.5 GiB，按低空间规则仅做小量诊断与文档工作。
