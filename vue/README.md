# BK群控控制台（基于 PIGEON）

## DEVELOP

```bash
npm ci
npm run dev
```

## BUILDING

```bash
npm run build
```

构建结果位于 `vue/dist`，不提交构建产物。仓库根目录的 Dockerfile 会自行构建此目录并复制到镜像；部署现有服务器的前端可使用 `ops/deploy_frontend.py`，见 `docs/BK-BRANDING.md`。

品牌入口为 `src/components/BrandIdentity.vue`、`index.html`、登录页和语言文案。`public/bk-logo.png` 保留用户提供的原始图片，同时用于站点图标；上游许可保存在 `public/upstream-license.txt`，不修改 SDK 名称、协议或配置键。
