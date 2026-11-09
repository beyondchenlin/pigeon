# BK群控品牌与前端部署

此变更基于 `dev`。`main` 与 `upstream/main` 不随测试部署更新。

## 品牌范围

- 控制台侧栏、登录页、标签页标题、站点图标与网页描述统一为 BK群控。
- 原始 Logo 位于 `vue/public/bk-logo.png`，未经重绘或修改；深色模式使用白色衬底保持可读。
- 中英文界面均保留品牌名 BK群控。
- 上游 LICENSE 保留，前端发布包包含 `upstream-license.txt`，登录页可查看。
- FIRERPA SDK 类名、`[pigeon]` 配置节、认证和设备协议不作品牌替换。
- `/d/<id>/` 是手机提供的 FIRERPA 远控界面，不属于此仓库的 Vue 前端。本次不修改手机软件。

## 构建与发布

```powershell
cd vue
npm ci --prefer-offline --no-audit --no-fund
npm run build
```

`vue/dist` 为唯一前端发布目录。旧的已跟踪 `html/` 构建删除，后续不向 Git 提交编译产物。完整 Docker 构建由前端阶段生成 `/tmp/build/html`，供原有后端构建使用。

现有测试服务器仅发布前端：将 `dist` 内容打包为 tar.gz，连同 SHA-256 校验值和提交号交给 `ops/deploy_frontend.py`。该脚本使用现有 `/user` 挂载保存版本，通过 nginx 静态目录切换与平滑重载生效，保留当前 Python 补丁、认证、数据库及手机转发连接。

```sh
python3 deploy_frontend.py /path/to/frontend.tar.gz --sha256 <archive-sha256> --revision <dev-commit>
```

脚本仅适用于当前 `phone-pilot-pigeon-pigeon-1` 部署布局。先校验归档与静态资源，再检测 nginx 配置，失败恢复原配置。发布后仍须浏览器验收登录页、设备列表与原有远控入口。

首次发布的回退方式：将 `/opt/phone-pilot-20261108/pigeon/single-phone-nginx.conf` 中静态资源 `root` 恢复为 `/var/www/html`，执行容器内 `nginx -t` 后平滑重载。镜像内原始前端作为镜像层存在，删除它不会释放镜像层空间，不为本次品牌修改重建整套后端。

本次发布产生的上传压缩包及替换下来的独立前端构建，验收后按精确路径清理；运行中的发布目录保留。旧镜像、数据库和既有恢复证据不在清理范围内。
