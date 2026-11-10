# 画面回传优化与点对点控制核查

本轮只修改 `dev` 的远控传屏实现，保持页面布局、触控坐标和设备权限。手机为 vivo V2220A、FIRERPA 10.9、Android shell UID 2000，未 Root。没有更换云服务器。

## 已采用的实现

- WebRTC 画面交给页面内的原生 `<video>` 合成显示，取消每帧 `video → Canvas` 复制。原 Canvas 保留为透明触控层和 JPEG 回退层，二者使用相同尺寸、位置及旋转坐标逻辑。
- 浏览器支持时把 `RTCRtpReceiver.jitterBufferTarget` 设为 20 ms；不支持或 setter 拒绝时继续使用浏览器默认行为。这是缓冲偏好，不能保证实际缓冲只有 20 ms。
- 实机对比后，把请求帧率上限从 10 调为 20 FPS，将理想采集间隔从 100 缩到 50 ms；仍使用 backend=1、scale=0.5、quality=25。只在打开远控时请求该媒体流，不改变设备卡片的缩略图策略。
- 保留首帧确认、ICE 宽限、重连退避和 JPEG 最新帧回退。收到真实 RTC 帧后才退出回退；关闭页面会释放轨道、socket、帧回调和定时器。
- `?renderer=canvas` 可单独诊断旧显示路径；它仍采用新版采集/缓冲参数，不等于整版回滚。`?transport=ws` 保留显式 JPEG 兼容方式。

## 开源和官方来源

| 项目 / 来源 | 核查到的做法 | 在本项目中的取舍 |
| --- | --- | --- |
| [scrcpy 视频文档](https://github.com/Genymobile/scrcpy/blob/master/doc/video.md)、[维护者对丢弃旧帧的解释](https://github.com/Genymobile/scrcpy/issues/2750) | 减少额外显示缓冲；消费不过来时跳过旧帧，避免延迟累积 | 保留 JPEG 最新帧策略，取消 RTC 的 JS 逐帧复制；其 USB/局域网延迟数字不能当作我们的公网承诺 |
| [Selkies 的 WebRTC 实现](https://github.com/selkies-project/selkies/blob/104edc1951efe27659988ca0b5763c33a32468e8/web/selkies-web-core/lib/webrtc.ts) | 媒体直接挂到 video；有序 input 和无序 pointer 数据通道分工 | 采用浏览器原生显示思路；它面向 Linux 桌面，不能直接装到 Android 替代现服务 |
| [Webscreen](https://github.com/huonwe/webscreen)、[数据通道控制实现](https://github.com/huonwe/webscreen/blob/5dc2dbe96a0722f5a6b4cad44c3cb5decdd955ab/webservice/webRTCManager.go) | 支持 Android Termux/scrcpy；接收 control-ordered / control-unordered 消息并交给 agent.HandleEvent | 可作为下一阶段手机端 P2P 控制桥接的参考；本轮没有安装替换整套系统，也没有复制其源码 |
| [ws-scrcpy](https://github.com/NetrisTV/ws-scrcpy) | 提供 WebCodecs 浏览器解码路径 | 说明浏览器原生媒体能力值得优先使用；其 ADB/WebSocket 结构本身不意味着手机与浏览器端到端直连 |
| [MDN jitterBufferTarget](https://developer.mozilla.org/en-US/docs/Web/API/RTCRtpReceiver/jitterBufferTarget)、[jitterBufferDelay](https://developer.mozilla.org/en-US/docs/Web/API/RTCInboundRtpStreamStats/jitterBufferDelay) | 接收缓冲偏好与累计统计的含义 | 能力检测；用统计区间增量除以该区间输出帧数，不把累计秒数当作单帧延迟 |
| [FIRERPA 远程桌面文档](https://device-farm.com/docs/en/remote-desktop)、[官方仓库](https://github.com/firerpa/lamda) | WebRTC、编码参数和 WebSocket 嵌入接口 | 继续复用当前手机服务；未找到可直接采用的公开 DataChannel 触控协议 |

检索包含 GitHub 和 X。X 未找到足够具体、可核验并可直接采用的实现细节；结论依据上述原始代码、官方文档和本机实测。没有把别的项目问题当作本机故障的已证实根因。

## 点对点到底覆盖哪些链路

当前实际链路为：

```text
画面：手机 ←→ WebRTC / UDP ←→ 浏览器（本次选中的候选对为 direct）
触控：浏览器 → HTTPS/WSS 云网关 → FRP → 手机 /ws/screen/touch
文字、主页等命令：浏览器 → 云网关 → 手机 /ws/command
```

在专用验收页面中，给真实手机的 WebRTC offer 加入一个不发送业务消息的 `bk-capability-probe` 数据通道。手机 answer 接受 `m=application ... webrtc-datachannel` / `a=sctp-port:5000`，随后通道进入 `open`。这证实 SCTP 数据传输可建立，**没有证实该通道已有点击、滑动处理器**。原生 app.js 与当前前端均使用 `/ws/screen/touch`，未发现原生前端创建 DataChannel 的代码。本轮不向未知协议发送手势，不宣称已经上线 P2P 输入。

下一阶段应验证手机端控制桥接：鉴权后的信令协商 → 手机侧 DataChannel 接收器 → 本机触控接口。桥接要放在手机或手机所在网络侧；若终点仍在香港云端，只是把浏览器到香港那段改成 WebRTC，并没有消除云端到手机的绕行。保持 down/up/key/文字有序；移动事件合并或有序号丢弃旧位置，并确保 up/取消不会先于最后一次有效移动。断连释放手指，重连不能重放旧操作；保留现有 WSS 回退，避免一条指令被两条通道重复执行。

浏览器与手机的 NAT/网络可能无法穿透，需要 [TURN 中转](https://webrtc.org/getting-started/turn-server)；不能承诺每个 5G 网络都直连。国内节点可能改善 WSS 控制或 TURN 中转路径，但不能修复手机进程阻塞，也不会自动加速已经直连的视频。

## 实机测量及限制

使用同一台手机的 Chrome 打开一次性黑白切换测试页，点击仅改变测试页颜色，不触碰私信或发布。页面另有移动色块，避免把静止屏幕的低帧率误当成传屏故障。端到端计时从浏览器发送 touch up 到接收到对应颜色帧；不是媒体 RTT，也不是摄像机测量的物理屏幕亮起时间。

验收浏览器即使报告 visible/focus，也出现约每秒一次的视频合成回调；原生显示测量一度得到约 1013 ms，不能拿该值与旧路径的 watchdog 绘制时间直接比较。后续对照统一临时开启 16×16 screencast 使合成器保持活动，结束立即关闭；未保存视频。该测试条件有别于用户自己的安卓 Chrome/Edge，结果不能保证所有终端和运营商相同。

| 条件 | 样本数 | 点击回传中位数 | P95 | 区间平均接收缓冲 |
| --- | ---: | ---: | ---: | ---: |
| 修改前实际部署，Canvas、默认缓冲、10 FPS；未统一合成条件 | 12 | 549 ms | 695 ms | 149 ms |
| Canvas、默认缓冲、10 FPS；合成器活动的对照 | 12 | 329 ms | 445 ms | 45 ms |
| 原生 video、20 ms 缓冲偏好、10 FPS；同样合成条件 | 12 | 301 ms | 366 ms | 29 ms |
| 原生 video、20 ms 缓冲偏好、20 FPS；协商后短样本 | 12 | 263 ms | 363 ms | 25 ms |
| 原生 video、20 ms 缓冲偏好、20 FPS；运行超过一分钟的样本 | 20 | 284 ms | 349 ms | 33 ms |
| 正式发布后，用户暂停其他操作、确认测试页后的复测 | 12 | 330 ms | 895 ms | 57 ms |

各组为顺序测试，公网状态变化，不是严格随机对照。不能用首行与末行宣称固定下降 48%。20 FPS 稳态样本中解码约 0.57 ms/帧，未增加丢包、解码丢帧或冻结；实际接收约 20 FPS，该测试图案约 0.48 Mbps。真实视频内容的带宽可能更高。状态命令往返仍常见约 230–530 ms，这不是触控 ACK，不能直接从点击总延迟中相减，但说明云端命令链路值得继续优化。

正式发布后的独占复测不覆盖上述前期结果：12 次样本实际接收约 20 FPS，媒体 RTT 28–29 ms，区间解码约 0.47 ms/帧，没有新增丢包或解码丢帧，但增加一次约 0.46 秒冻结；P95 在这个小样本中等于最慢一次。正式复测前还捕捉到命令往返约 9–13 秒、媒体丢包/冻结累计增加，随后未重启便恢复至约 275–497 ms。当时有人同时操作，浏览器合成也受限，不能将全部异常归因于单个网络或编码因素。服务器 load average 0.05，PIGEON 容器 CPU 约 1.39%，缺乏服务器 CPU 饱和证据。手机 CPU 历史窗口存在其他应用负载，但并非异常瞬间采样，不能据此判定根因。

因此本轮验收确认新版功能与 20 FPS 请求生效，**没有通过“公网长时间低抖动”验收**。下一步以手机端 P2P 输入桥接及媒体排队/生命周期观测为重点，保留 WSS 回退；仅依据媒体 RTT 低就迁换服务器，或承诺固定 0.28 秒操作响应，都不成立。最终有效数据为 `final-deployed-confirmed-target-20fps.json`；前一次未确认测试页的采样没有有效样本，不计入结果。

实机浏览器故障注入：临时关闭当前 RTC 并阻止新的 RTC 创建，真实 JPEG 画面约 1.7 秒恢复；解除限制后自动回到真实 WebRTC。此项是浏览器媒体故障回退验收，不冒充手机飞行模式或跨运营商断网验收。移动端 390×844 视口中，视频与触控层尺寸相同、位置误差小于 0.01 px，没有横向溢出；保持完整比例，没有裁切手机画面。主页按钮与触控滑动已在当前手机执行。

原始测量及截图留在本机 `phone-pilot-20261108/browser/video-optimization-20261110`，不提交屏幕内容、证书或原始日志。自动化包括 30 项视频/输入/布局/视口测试、vue-tsc 和 Vite 构建；随后帧率修改复跑 17 项视频测试及构建。

## 验收中复现的独立阻塞，尚未根治

切换对照连接时，网页控制/媒体无法重新接通，设备心跳仍在。通过既有认证 RPC 在手机本地检查，普通及带设备证书的 TLS 握手均超时。新出现的 Python 子进程 PID 31068、父 PID 18561、启动 ticks 41756633、UID 2000、可执行文件属于当前 FIRERPA，主线程及另一个线程均停在 futex 等待。

核对上述身份及仍然超时后，只对这个子进程先发 SIGTERM；未退出再 SIGKILL。手机主服务没有重启，本地 TLS 随即恢复 TLSv1.3，网页自动恢复。没有安装定时杀进程脚本，没有把这种恢复措施包装为根治。其他等待状态的进程未随意批量结束。

目前没有取得该原生模块内部堆栈；不能证明是某一个编码库、fork 锁或浏览器关闭动作的具体缺陷。本次重现意味着长期稳定性验收仍有未解决项。下一阶段优先补齐可观测的手机端媒体/控制生命周期，核查原生退出阻塞；必要时以 Webscreen/scrcpy 一类独立手机端媒体控制实现做单机替代试验，保留现服务回退。仅缩小前端缓冲不能消除这个故障。

## 部署记录

- `2ec780d4ea31a2705b042c562d0e1d9eee09da2b`（原生 video + 接收缓冲偏好）已部署，静态目录原子切换及 nginx 检查通过，未重启后端或手机主服务。
- 2026-11-10 通过用户已登录的阿里云命令助手续期原有任务 SSH 密钥 24 小时，保留其他授权，未重置 root 密码或重启服务器。命令执行成功、ExitCode 0，随后独立 SSH 登录验证通过。
- `b7a44e4420230eb6585e7819d442e3773606ce23` 已部署，传输包 SHA256 为 `3161fd9a397b46faf6242f45a1e3731ee78c73623cf04baab1f0588c42db309e`。正式页直接发出 fps=20 / scale=0.5 / quality=25，未用诊断参数覆盖；原生 video 为 540×1194，选中 srflx ↔ srflx / UDP 直连，首次读取 RTT 31 ms。nginx 配置校验通过，保持前端原子切换。
- 390×844 视口复核：视频和触控层均为 381.708×844、横坐标 4.146，完全重合，无横向溢出。正式页已执行真实测试页点击和主页操作；未发送私信、发布内容或修改手机权限。
- main 保持 `e282a5aa9cea9f7ded61cc6a9654f79476119b66`。复用原工作树、node_modules、dist，无新增工作树或运行环境。
- 部署后清理本任务已核验的传输包与一次性测试页：本机 5,397,308 字节，服务器 10,795,344 字节（均为逻辑文件大小，不当作磁盘净释放量）。全部 release 目录、回滚元数据及本地验收证据保留。阿里云续期命令作为操作记录保留；无定时清理任务。
