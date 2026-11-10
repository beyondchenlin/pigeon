# Android Chrome / Edge 可见区域与全屏修复

部署代码：`8e9c5444f4d6c8f5c741108feccb40fdd8915022`（dev）。main 保持 `e282a5aa9cea9f7ded61cc6a9654f79476119b66`。

## 用户操作

打开 https://dongfengqi.top/d/umdax0nurk9o/ ，刷新后点右上角“全屏”。浏览器接受请求后进入全屏并收起工具；右侧“工具”可恢复按钮，然后点“退出全屏”。退出时恢复普通视图，画面按当前可见区域等比显示。不固定缩放到 80%，不裁切、不拉伸。

全屏必须从用户点击触发，不能保证初次打开网页时自动隐藏浏览器栏。拒绝或不支持全屏时保留普通视图并提示。全屏对象是整个文档，确保传送到 body 的输入和工具面板仍然可用。

## 原因与改动

- 原实现只监听可见高度，部分 fixed/absolute 元素仍定位到布局视口，未处理 visualViewport 的偏移和 scroll 事件。
- 新增共享、按动画帧合并的可见区域监听，记录宽高和四侧边界；后台外框、导航、远控画面、输入面板与设备属性面板使用相同边界。
- Chrome / Edge 支持时通过 `interactive-widget=resizes-content` 请求软键盘同时缩小布局区域；仍保留 VisualViewport 回退。
- 不拦截原生双指缩放。放大时暂停自动重新适配，避免抵消用户的放大操作。
- 全屏仅更新前端视图；未改传屏协议、设备服务、Root 状态或账号。

依据：[Chrome 官方视口与键盘说明](https://developer.chrome.com/blog/viewport-resize-behavior)、[MDN requestFullscreen](https://developer.mozilla.org/en-US/docs/Web/API/Element/requestFullscreen)。

## 验收与边界

| 检查 | 结果 |
| --- | --- |
| Node 专项检查 | 27 项通过：viewport、mobile layout、phone input、phone video |
| TypeScript / 生产构建 | `npx vue-tsc --noEmit`、`npm run build` 通过；保留原有大包提示 |
| Chromium Fullscreen API | 真实点击进入文档全屏、打开输入面板、退出后恢复工具通过 |
| 不支持全屏 | 浏览器内模拟 capability=false；提示且保留普通画面通过 |
| 布局高度不变的遮挡回归 | 布局 844px，可见高度 690/780/430/320/844px，偏移 0/72/120px；真实生产 DOM 的画面、头部、底部工具都在可见区域内 |
| 输入面板 | 可见 430px + 偏移 72px；可见 300px + 偏移 100px，面板限制高度并可内部滚动；恢复后正常 |
| 管理后台 | 设备、总览、作业、脚本、模型、用户六页在可见 530px + 偏移 40px 时外框、头部、底栏都未越界；创建用户抽屉在可见 340px + 偏移 80px 时未越界，未提交创建 |
| 三种设备视图 | 390×844 布局、690px 可见高度：多卡片完整显示 4 台、列表 6 台、单卡片 1 台。100 台仅为浏览器内离线排版样本，不是 100 台实机压测 |
| 当前真实手机 | vivo V2220A 实际画面；点击最近任务后截图确认进入任务页，随后返回主页；收尾 WebRTC live，电量 82% 充电中 |
| 桌面布局 | 1440×900 截图确认左上品牌和左右布局保持；增加全屏入口 |

上述视口和键盘遮挡通过浏览器内覆盖 VisualViewport 读数进行回归，未冒充真实 Android 浏览器上下栏。实际安卓 Chrome / Edge 的地址栏、底部栏、系统键盘及厂商全屏策略，需用户在自己的操作手机刷新后确认。当前实机用于被控端，桌面 Chromium 用于操作端。未在本任务重新量化端到端延迟。一次累计帧数阈值等待超时，随后截图确认远端已进入最近任务，最终 WebRTC 在线；该等待不作为延迟验收结论。

证据位于 `E:/qk/phone-pilot-20261108/browser-viewport-fix-20261110/`：fullscreen.json、viewport-bounds.json、admin-bounds.json、admin-dialog.json、gallery-modes.json、final-live.json 和必要截图。浏览器测试空间已关闭。

## 发布、回滚与磁盘

沿用 `/opt/phone-pilot-20261108/deploy_frontend.py` 校验 SHA-256、切换前端 current 符号链接、nginx -t 后平滑 reload。未重启手机或后端容器。

- 当前前端 release：`8e9c5444f4d6c8f5c741108feccb40fdd8915022`。
- 上一版回滚 release：`bccc7d0c998f3c035783df63b4d5fa62d75124da`，与部署脚本的 before 记录一起保留。用途为本次前端回滚；负责人：本项目维护者；2026-11-17 复查。更早的已有回滚资料未动。
- 复用 `E:/qk/pigeon`、`vue/node_modules`、已有工具链；没有新增 worktree。
- 构建前 C 盘 19.26 GiB、E 盘 63.34 GiB；服务器根盘可用 27 GiB。构建及传输远低于 1 GiB。
- 本次本机和服务器各一个 5,396,033 字节部署 tar 包在上线验收后定向清理；本机 CLI 指南副本 24,438 字节也清理。源码、生产构建、回滚 release、验收证据保留。清理记录与实际磁盘差异见任务目录 cleanup-result.json；不把逻辑大小当净释放量。
