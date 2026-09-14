# Docker 生产部署

本章采用当前的低成本单机方案：ECS 上由 Docker Compose 运行 FastAPI、Nginx、PostgreSQL 和
Redis 四个容器。示例需要先在测试环境验证，再根据 ECS 系统和实际域名调整。若未来改用 RDS/Tair，
请先完成数据迁移，再移除本机两个依赖服务，不能只修改连接地址。

## 1. 建议的服务器目录

```text
/opt/fastapi-blog/
  current/                 # Git 工作树或发布包
  config/app.env           # FastAPI 运行时变量，权限 600
  config/compose-prod.env  # Compose 变量和数据库/Redis密码，权限 600
  data/postgres/           # PostgreSQL 数据目录
  data/redis/              # Redis AOF 数据目录
  data/media/              # 用户上传文件，挂载进容器
  secrets/tls/             # 证书和私钥
  nginx/default.conf       # Nginx 站点配置
```

说明：仓库内的 `app/static/`（CSS、JS、Logo、favicon）随 Docker 镜像发布，不在 `data/` 下单独维护。
不要建立 `/opt/fastapi-blog/data/static` 并挂载到 `/app/app/static`；该挂载会遮住镜像内的新文件，
是生产环境出现“代码更新但样式/图片没更新”的常见原因。

备份目标至少包括 `data/postgres`、`data/redis`、`data/media` 和两个部署配置文件；不要只备份 Git 仓库。

## 2. Dockerfile

仓库根目录已经提供可执行的 `Dockerfile`。下面保留关键结构用于讲解；部署时应直接使用仓库
文件，并随代码版本一起审查和发布：

```dockerfile
# 固定 Python 小版本，升级时由测试和镜像发布流程显式完成。
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# uv 官方镜像只提供二进制，避免 curl | sh。
COPY --from=ghcr.io/astral-sh/uv:0.11.9 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini README.md ./
RUN uv sync --frozen --no-dev

# 非 root 用户运行应用；挂载 media 后宿主目录也必须允许该 UID 写入。
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/app/media \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# 构建阶段已经同步依赖，运行阶段不要再次同步 uv.lock；生产 app 只接受内部 Nginx 流量。
CMD ["uv", "run", "--no-sync", "fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
```

固定 `uv` 镜像版本时，应选择已经在开发/CI 验证的版本，不要长期使用 `latest`。

仓库根目录同时提供 `.dockerignore`：

```text
.git
.venv
__pycache__
*.pyc
.env
.env.*
!.env.example
fastapi_blog.db
tests
docs
logs
app/media/*
```

`.dockerignore` 防止密钥、开发数据库、历史上传和 Git 元数据进入镜像层。

## 3. Compose 配置

仓库根目录已经提供 `compose.production.yaml`，并通过变量指定环境文件、域名、证书目录和媒体
目录。下面是结构说明；部署以仓库文件为准：

```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    image: fastapi-blog:${APP_IMAGE_TAG:-local}
    env_file:
      - /opt/fastapi-blog/config/app.env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    expose:
      - "8000"
    volumes:
      - /opt/fastapi-blog/data/media:/app/app/media
    restart: unless-stopped
    init: true
    healthcheck:
      test:
        - CMD
        - /app/.venv/bin/python
        - -c
        - >-
          import json, os, urllib.request;
          host=json.loads(os.environ['ALLOWED_HOSTS'])[0];
          request=urllib.request.Request(
            'http://127.0.0.1:8000/health/ready', headers={'Host': host});
          urllib.request.urlopen(request, timeout=3)
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 40s
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "5"
    networks: [backend]

  nginx:
    image: nginx:1.28-alpine
    depends_on:
      app:
        condition: service_healthy
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /opt/fastapi-blog/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - /opt/fastapi-blog/secrets/tls:/etc/nginx/tls:ro
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "5"
    networks: [backend]

networks:
  backend:
    driver: bridge
```

当前仓库实际的 `compose.production.yaml` 还包含 `postgres` 和 `redis`，它们不映射宿主机端口，数据
分别挂载到 `PG_DATA_PATH` 和 `REDIS_DATA_PATH`。上面的片段只用于理解 app/nginx 关系，部署时必须
以仓库文件为准。

镜像标签应使用 Git commit 或版本号，不要只用 `latest`。当前 WebSocket 广播状态保存在进程
内存，所以保持一个 app 容器、一个 worker；不能简单增加 replicas 或 Uvicorn workers。

启动前至少设置：

```bash
export APP_DOMAIN=blog.example.com
export APP_IMAGE_TAG=$(git rev-parse --short HEAD)
export APP_ENV_FILE=/opt/fastapi-blog/config/app.env
export MEDIA_HOST_PATH=/opt/fastapi-blog/data/media
export TLS_HOST_PATH=/opt/fastapi-blog/secrets/tls
export PG_DATA_PATH=/opt/fastapi-blog/data/postgres
export REDIS_DATA_PATH=/opt/fastapi-blog/data/redis
sudo install -d -o 10001 -g 10001 "$MEDIA_HOST_PATH"
# postgres:16-alpine 使用 UID 70，redis:7-alpine 使用 UID 999；目录属主不正确会导致容器反复重启。
sudo install -d -o 70 -g 70 -m 700 "$PG_DATA_PATH"
sudo install -d -o 999 -g 1000 -m 700 "$REDIS_DATA_PATH"
```

`TLS_HOST_PATH` 中必须存在 `fullchain.pem` 和 `privkey.key`。环境变量只影响 Compose 路径与模板，
应用的 JWT、Cookie、邮件等运行时配置来自 `APP_ENV_FILE`；数据库和 Redis 的账号、密码、主机由
`compose-prod.env` 注入 Compose，再由 `compose.production.yaml` 生成 `DATABASE_URL`/`REDIS_URL`。
因此正式容器中的主机必须分别是 `postgres:5432` 和 `redis:6379`，不要把旧的托管地址留在 `app.env`。

## 4. 首次发布

```bash
cd /opt/fastapi-blog/current
COMPOSE_ENV=/opt/fastapi-blog/config/compose-prod.env
dc() { docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml "$@"; }
dc config -q
dc build --pull app
dc up -d postgres redis
dc run --rm app uv run --no-sync alembic current
dc run --rm app uv run --no-sync alembic upgrade head
dc up -d app nginx
dc ps
dc logs --tail=100 app nginx postgres redis
```

迁移前必须：确认 `DATABASE_URL` 指向本机 `postgres` 服务、备份 PostgreSQL 数据目录或逻辑备份、审查所有待执行 migration。
本项目的 Redis 切换迁移会删除旧 `refresh_sessions` 表，使已有 Refresh Token 失效，用户需要
重新登录，但不应影响用户和文章数据。

## 5. 日常发布

```bash
git fetch --all --prune
git checkout <已经测试的提交或标签>
COMPOSE_ENV=/opt/fastapi-blog/config/compose-prod.env
dc() { docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml "$@"; }
dc config -q
dc build --pull app
dc run --rm app uv run --no-sync alembic current
dc run --rm app uv run --no-sync alembic upgrade head
dc up -d --remove-orphans
dc ps
```

`build app` 是静态文件同步的关键步骤：Dockerfile 会把当前提交的 `app/static` 一起复制进新镜像。
仅执行 `up -d` 不会自动把工作树变化写入已存在的镜像。发布后可检查：

```bash
docker compose --env-file /opt/fastapi-blog/config/compose-prod.env -f compose.production.yaml exec app sh -c \
  'test -f /app/app/static/css/site.css && test -f /app/app/static/images/logo.png && test -f /app/app/static/images/favicon.ico'
curl -fsSI "https://$APP_DOMAIN/static/css/site.css"
```

不要在生产服务器直接修改源代码。发布前在 CI/本地至少执行 `uv run pytest -q` 和
`uv run ruff check app tests migrations`。应用镜像回滚不等于数据库回滚；如果迁移不向后兼容，
应先设计 expand/contract 迁移，而不是现场执行破坏性 downgrade。

## 6. 单机自建服务的维护底线

当前 Compose 已包含 PostgreSQL 和 Redis，并把端口只暴露在内部网络。正式使用仍必须自行完成：
独立数据盘、异机备份、备份校验、监控、版本升级、磁盘容量告警和恢复演练。只做
`restart: unless-stopped` 不等于高可用；ECS 故障会同时影响四个服务。
