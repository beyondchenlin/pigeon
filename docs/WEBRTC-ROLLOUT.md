# BK 远控 WebRTC 接入

本次仅修改 `dev` 的传屏与恢复逻辑，保持现有布局和品牌尺寸。上一版恢复点是 `bk-v0.2.0-control-ui`。

## 连接行为

打开页面先通过 H.264 WebSocket 显示画面，同时通过 FIRERPA 的 `/ws/webrtc/signaling` 协商 WebRTC 视频轨道。收到可绘制的首帧后才关闭备用视频连接。首帧 12 秒超时、ICE 失败或持续断开 3 秒后保留 / 恢复 WebSocket，并按 2、5、15、30 秒间隔重试 WebRTC。暂停、页面隐藏、设备离线和组件卸载会取消所有连接和重试。

触控连接独立恢复，视频故障不再强制中断触控。滑动 move 事件按浏览器动画帧合并，down / up 保持顺序；重连先发送 reset，避免遗留按下状态。

兼容视频队列按数量和等待时间限制，超限重新建立完整 H.264 码流，不丢弃任意依赖帧；软件解码持续失败才改用 MJPEG。正常静止画面不再因为 15 秒没有绘制而被强制重连。

已有状态位置显示当前传输方式和“媒体 RTT”，这个数值不是点击端到端延迟。连接日志记录回退原因。地址临时追加 `?transport=ws` 可在当前页面关闭 WebRTC，方便故障回退与同参数对照，不修改手机配置。

## 网络配置

接入初期的 Wi-Fi 网络实测 ICE 直连探测无响应，因此补充自建 coturn；后续移动网络验收成功使用 UDP 直连，不能把早期失败推广到所有网络。`/api/v1/device/{id}/ice` 在校验登录和设备权限后发放一小时有效的 HMAC 凭据，禁止缓存；共享密钥只保存在服务器。每次 WebRTC 重试重新获取配置，接口超时仍保留 WSS 并尝试直连。媒体优先选择可用直连，受限网络通过 TURN 中转，全部失败时回退鉴权 WSS。

现有香港服务器运行独立 `bk-turn.service`，开放 TCP / UDP 3478 和 UDP 49160–49200；禁用匿名中转、TCP peer relay、内网 / 环回 / 组播目标，限制会话及带宽。不会启动 STF 应用。`ops/install_bk_turn.py` 记录部署配置；只重启 API worker 注册新接口，不重启手机服务或整个 PIGEON 栈。TURN 目前使用 UDP 或 TCP 入口，媒体仍由 WebRTC DTLS-SRTP 加密；不宣称已经覆盖只允许 TLS 443 的极端网络。

依据：[WebRTC 官方 TURN 说明](https://webrtc.org/getting-started/turn-server)、[coturn 配置文档](https://github.com/coturn/coturn/blob/master/README.turnserver)。

本机为 `dongfengqi.top` 和 `*.dongfengqi.top` 增加 SpeedCat 持久直连名单和 Windows WinINET 绕过规则。原有代理开关、节点及其他规则保留；浏览器新请求已核对为服务器直连。换另一台电脑需为该电脑的代理单独配置此域名，网站代码不能更改访问者的系统代理。

## 验证

- `node --test tests/phone-video.test.mjs`：10 项，覆盖真实首帧切换、ICE / answer 顺序、异步取消、超时清理、媒体与信令故障区分、断线宽限期，以及备用视频切换 / 恢复后的资源释放、凭据接口失败和延迟返回。
- `ops/check_bk_ice.py` 在现有容器检查短期凭据签名、随机标识、密钥不外泄和权限先于签发；原 telemetry 检查保持通过。
- `vue-tsc --noEmit`、项目原有 `npm run build`。
- 使用当前手机检查 WSS / WebRTC、回退 / 重连、暂停、页面恢复及原有触控。帧率用实际绘制计数；点击延迟必须以同一浏览器时钟记录输入和可识别画面变化，单独报告样本范围。

部署采用原有前端发布脚本，保留旧版本并平滑重载 Nginx；不重启服务器、不改 `main`。
