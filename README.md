PIGEON is an open-source, locally-deployed Android device management and automation scripting system, supporting P2P/FRP cross-network connectivity, script development, online debugging, realtime task dispatch, execution, and tracking, ideal for remote device ops and RPA/AI scripts.

## 部署 BK 二开版

在新电脑或服务器上安装 BK 群控，请按[独立部署说明](deploy/standalone/README.md)操作。Docker Compose 默认从 GitHub Container Registry 拉取已构建的 BK 镜像，并为每个实例生成单独的登录和设备凭据。

下面保留上游部署步骤。原版 Compose 默认使用 `firerpa/pigeon:1.0`。

## BUILDING

```bash
docker build -t firerpa/pigeon:1.0 . # or pull from docker hub
```

## DEPLOYMENT

```bash
cp -pr docker ~/pigeon

cd ~/pigeon
cp .env.example .env

# !! Edit the .env file according to your own environment (see .env.example for more instructions).

docker compose -f docker-compose.yml up --force-recreate -d
```

Visit https://${LOCAL_IP/PUBLIC_IP}:8000 and log in with default credentials: username `admin`, password `pigeon`.
