# 点对点触控与媒体进程改造

分支：`dev`。不修改 main、页面布局或手机 Root 状态。

## 链路

浏览器在原视频 PeerConnection 中创建有序 `bk-touch-v1` DataChannel。手机扩展仅接受经原有已认证信令创建的通道，将 down/move/up 转到本机原生触控接口。公网段与视频使用同一个 ICE/DTLS 连接；是否真正直连必须检查选中的候选对，经过 TURN 时仍属中转，不能仅凭“WebRTC”字样判断。

文字输入、导航键、设备属性和信令仍使用原有云端接口，本次没有把全部命令改成点对点。旧手机没有扩展时继续使用 WSS。切换等待当前手势结束；通道失效释放触点，等待手机租约过期后再回退，不重放中断的点击或滑动。

手机扩展借用原生信令请求中的设备令牌，连接固定的本机 TLS 触控端点；没有增加公网监听、禁用云端鉴权或给 DataChannel 增加 shell/任意命令能力。健康接口沿用 BaseHttpExtension 的鉴权。日志只记录生命周期与计数，不记录令牌、SDP、输入文字或坐标。

## 媒体阻塞的证据与改动

此前故障已记录：手机 RPC 正常但本地和远程 TLS 均阻塞，主服务下一个 Python 子进程长时间等待 futex；只结束该子进程后恢复。详见 `VIDEO-P2P-OPTIMIZATION-20261110.md`。当时没有阻塞栈，不能把 gRPC/fork 死锁写成已被完整证明的唯一根因。

本次确认原生 WebRTC ScreenProcess 使用 asyncio 子进程启动，未传 preexec_fn；手机 Python 没有 os.posix_spawn，Bionic libc 提供该接口。扩展只替换媒体启动路径，使用 Bionic 的 POSIX_SPAWN_USEVFORK 和原生文件操作，避开 Python/多线程服务中的 fork 初始化路径。保持原生编码命令、就绪检测和 stderr 消费；精确回收由本扩展创建的编码进程，不安装定时杀进程或重启服务任务。

原生 H264Reader 队列上限为 64 个编码包。本次改成最多 4 个、最长 200 ms；超过限额丢弃过期 GOP，等待可独立解码的 IDR，同时保留 SPS/PPS 和媒体时间戳。不能随意丢任意 P 帧后继续解码。此改动防止积压旧帧，不保证网络丢包或关键帧间隔造成的等待全部消失。

## 安装与恢复

`ops/install_bk_rtc.py` 只允许 Android shell UID 2000，检查两个 FIRERPA 10.9 原生模块的 SHA-256，安装到现有 USRDIR/modules。遇到其他版本或不认识的文件则拒绝覆盖，不能直接推广到未经验证的版本。

四个安装源文件放在同一暂存目录，执行：

```text
python3 install_bk_rtc.py --runtime-root <当前服务目录> --user-root <当前用户目录>
```

安装后通过 FIRERPA 官方停止/启动流程加载扩展。启动时仍使用原有配置、证书、数据库与 App 目录。回退加 `--rollback`，只移除安装清单中哈希匹配的三个文件，再按官方流程加载原服务。不得删除整个 modules 目录或手机个人数据。

日志：USRDIR/phone-pilot/bk-rtc.log，单文件 2 MiB，最多 3 份。安装清单与现有配置用于恢复，由项目负责人保留并于 2026-11-17 复查。

## 验证状态

开发检查：前端 32 项相关测试、Python 10 项协议/队列/授权/租约测试、vue-tsc 与 Vite 构建通过。手机隔离进程中 12 次 Bionic spawn 成功，文件句柄 7→7；扩展版本校验、原生编码包类型与队列接入检查通过。这些不等于正式媒体服务的验收。

后续已定位并修复启动完整性、环境继承、原生编码器路径、触控桥接端口及健康接口兼容问题，手机已恢复。纯 5G 15 分钟试验中完成两次断网自动重连、实际点击/滑动与无密码唤醒，主服务未重启，媒体子进程未重现启动阻塞。画面延迟与最长约 48 秒的重连等待仍未达标，不标记为长期稳定里程碑。详细数据、局限和后续定位方向见 [单手机纯移动网络验收记录](CELLULAR-ACCEPTANCE-20261110.md)。

## 参考

- [WebScreen WebRTC 实现](https://github.com/huonwe/webscreen/blob/5dc2dbe96a0722f5a6b4cad44c3cb5decdd955ab/webservice/webRTCManager.go)：媒体与输入通道结合的实现参考。
- [FIRERPA 集成接口](https://device-farm.com/docs/en/capability-integration)：原生远控输入与媒体接口。
- [AOSP Bionic spawn 实现](https://github.com/aosp-mirror/platform_bionic/blob/main/libc/bionic/spawn.cpp)及[接口声明](https://github.com/aosp-mirror/platform_bionic/blob/main/libc/include/spawn.h)：Android 原生 spawn 的行为与 ABI。
- [aiortc 1.15.0 媒体工具](https://github.com/aiortc/aiortc/blob/1.15.0/src/aiortc/contrib/media.py)：媒体线程与队列生命周期参考。
