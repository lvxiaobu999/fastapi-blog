# Docker 本机 PostgreSQL 与 Redis 正式部署教程

本文是当前博客的正式单机方案：一台 ECS 使用 Docker Compose 同时运行 FastAPI、Nginx、PostgreSQL 16
和 Redis。数据库和 Redis 不再购买 RDS/Tair，也不向公网暴露端口。

## 1. 架构和责任边界

```text
公网 80/443
    -> Nginx 容器
    -> FastAPI app 容器（仅 Compose backend 网络可见）
         |-> postgres 容器（5432，仅 backend 网络）
         `-> redis 容器（6379，仅 backend 网络）
```

| 服务 | Compose 服务名 | 宿主机端口 | 数据位置 | 访问方式 |
|---|---|---:|---|---|
| FastAPI | `app` | 不映射 | 镜像 + `data/media` | Nginx 反向代理 |
| Nginx | `nginx` | 80、443 | 证书只读挂载 | 公网 |
| PostgreSQL 16 | `postgres` | 不映射 | `PG_DATA_PATH` | app 使用 `postgres:5432` |
| Redis | `redis` | 不映射 | `REDIS_DATA_PATH` | app 使用 `redis:6379` |

`postgres` 和 `redis` 只在 Docker 内部网络提供服务。不要在 ECS 安全组开放 5432、6379，也不要在
Compose 中写 `5432:5432` 或 `6379:6379`，否则会把数据库暴露到公网扫描面。

单机自建的代价由你负责：ECS 故障会同时影响应用和数据服务；必须给 PostgreSQL 数据目录、Redis
数据目录和媒体目录做独立备份，并定期验证恢复。`restart: unless-stopped` 只负责进程异常后的重启，
不等于高可用、主从或自动备份。

## 2. 两个环境文件分别做什么

### `compose-prod.env`

位置：`/opt/fastapi-blog/config/compose-prod.env`。它由 Docker Compose 的 `--env-file` 读取，
负责 Compose 插值，包括：域名、镜像标签、数据库容器初始化账号/密码、数据目录和证书目录。
因为现在包含 `POSTGRES_PASSWORD` 和 `REDIS_PASSWORD`，建议使用 `root:docker` + `chmod 640`（仅 root 执行 Compose 时可用 600），不能提交 Git。

### `app.env`

位置：`/opt/fastapi-blog/config/app.env`。它由 `services.app.env_file` 注入 FastAPI，负责应用运行时
配置。单机 Docker 方案的 `DATABASE_URL`/`REDIS_URL` 由 Compose 根据 compose-prod.env 自动注入，
app.env 不再重复保存数据库和 Redis 密码。Compose 会使用 `postgres:5432`、`redis:6379` 服务名；
为了减少错误，密码建议仅含字母、数字、点、短横线和下划线。

## 3. 首次准备 ECS 目录

```bash
sudo install -d -m 750 /opt/fastapi-blog/config
sudo install -d -o 70 -g 70 -m 700 /opt/fastapi-blog/data/postgres
sudo install -d -o 999 -g 1000 -m 700 /opt/fastapi-blog/data/redis
sudo install -d -o 10001 -g 10001 -m 750 /opt/fastapi-blog/data/media
sudo install -d -m 750 /opt/fastapi-blog/secrets/tls
```

PostgreSQL/Redis 官方镜像首次启动时会检查并调整数据目录权限。如果某个镜像因宿主机目录权限失败，
先查看日志和容器内运行 UID，再只对对应目录授权；不要把目录改成 `777`。

安装配置文件：

```bash
sudo install -o root -g docker -m 640 /tmp/compose-prod.env \
  /opt/fastapi-blog/config/compose-prod.env
sudo install -o root -g docker -m 640 /tmp/app.env \
  /opt/fastapi-blog/config/app.env
```

证书文件名必须是：

```text
/opt/fastapi-blog/secrets/tls/fullchain.pem
/opt/fastapi-blog/secrets/tls/privkey.key
```

## 4. `compose-prod.env` 最小配置

仓库示例 [`deploy/compose-prod.env.example`](../../../deploy/compose-prod.env.example) 已包含字段说明；
真实 `deploy/compose-prod.env` 不提交 Git。至少确认以下值
已替换，不能保留尖括号：

```dotenv
APP_DOMAIN=www.sanwan.xyz
APP_IMAGE_TAG=production
COMPOSE_PROJECT_NAME=fastapi-blog-prod
APP_ENV_FILE=/opt/fastapi-blog/config/app.env

POSTGRES_USER=fastapi_blog
POSTGRES_DB=fastapi_blog_postgresql
POSTGRES_PASSWORD=<随机密码>
REDIS_PASSWORD=<随机密码>

PG_DATA_PATH=/opt/fastapi-blog/data/postgres
REDIS_DATA_PATH=/opt/fastapi-blog/data/redis
MEDIA_HOST_PATH=/opt/fastapi-blog/data/media
TLS_HOST_PATH=/opt/fastapi-blog/secrets/tls
```

注意：PostgreSQL 官方镜像只在数据目录为空时使用 `POSTGRES_USER`、`POSTGRES_DB` 和
`POSTGRES_PASSWORD` 初始化。目录已经有数据后，修改 Compose 变量不会重置账号或密码；密码变更需
进入数据库执行 `ALTER ROLE`，然后只修改 compose-prod.env 即可，Compose 会自动生成新的 app 连接串。

版本安全：当前镜像是 PostgreSQL 16。已经由 PostgreSQL 17 初始化的数据目录不能直接挂载给 16
启动（大版本降级会失败或造成数据风险）。若 ECS 旧目录来自 17，请先继续使用 17，或在独立的新
16 目录中通过 `pg_dump`/`pg_restore` 完成迁移；不要为了“版本一致”删除原目录，也不要直接覆盖。

## 5. `app.env` 最小配置

保留你自己的 JWT、邮件和 QQ 配置；数据库/Redis URL 由 Compose 注入，不在此文件重复填写：

```dotenv
ENV=production
PROJECT_TITLE=三碗博客
ALLOWED_HOSTS=["www.sanwan.xyz"]
PUBLIC_IP_MODE=false
AUTH_COOKIE_SECURE=true

REDIS_KEY_PREFIX=fastapi-blog:production
REDIS_SOCKET_TIMEOUT_SECONDS=2

SECRET_KEY=<至少32个字符的随机值>
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_MINUTES=10080
REFRESH_IDLE_TIMEOUT_MINUTES=1440

LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_TO_FILE=false
```

不要在同一个文件中同时保留旧的 RDS/Tair 地址和本机 `postgres`/`redis` 地址。每个变量只能有一个
生效值；Pydantic Settings 会按环境变量优先级读取，重复配置容易造成误判。

## 6. 首次启动顺序

在已经同步代码的目录执行：

```bash
cd /opt/fastapi-blog/current
export COMPOSE_FILE=compose.production.yaml
export COMPOSE_ENV=/opt/fastapi-blog/config/compose-prod.env
export APP_IMAGE_TAG=$(git rev-parse --short HEAD)
export COMPOSE_PROJECT_NAME=fastapi-blog-prod

dc() {
  docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" "$@"
}

test -r "$COMPOSE_ENV"
test -r /opt/fastapi-blog/config/app.env
test -r /opt/fastapi-blog/secrets/tls/fullchain.pem
test -r /opt/fastapi-blog/secrets/tls/privkey.key
dc config -q
```

`config -q` 只检查 YAML 和变量插值，不启动容器、不连接数据库。确认通过后构建 app 镜像：

```bash
dc build --pull app
```

Dockerfile 构建阶段已经用锁文件安装依赖，下面所有生产容器内的 `uv` 命令都带
`--no-sync`，避免一次性容器或健康检查在启动时重新同步依赖。新增依赖时应先更新 `uv.lock`
并重新构建镜像，不能在生产容器运行时临时安装。

构建完成后先启动数据库和 Redis，让它们完成初始化：

```bash
dc up -d postgres redis
dc ps postgres redis
dc logs --tail=100 postgres redis
```

预期两个容器为 `healthy`。如果 PostgreSQL 数据目录非空，日志中的“database system is ready to
accept connections”比“初始化完成”更重要；Redis 应能看到 ready to accept connections。

然后检查应用连接：

```bash
dc run --rm app uv run --no-sync alembic heads
dc run --rm app uv run --no-sync alembic current
```

首次空数据库确认目标名称和账号后，执行一次迁移：

```bash
dc run --rm app uv run --no-sync alembic upgrade head
```

最后启动 app 和 Nginx：

```bash
dc up -d app nginx
dc ps
dc logs --tail=100 app nginx
```

`app` 的 healthcheck 会执行 PostgreSQL `SELECT 1` 和 Redis `PING`；app 健康后 Nginx 才会接收流量。

## 7. 日常发布和重启

每次代码、CSS 或图片更新都必须重新构建 app 镜像：

```bash
cd /opt/fastapi-blog/current
git fetch --all --prune
git checkout <已经测试的提交或标签>
export APP_IMAGE_TAG=$(git rev-parse --short HEAD)

dc config -q
dc build --pull app
# 有迁移时先备份，再执行；没有待迁移则会保持当前结构。
dc run --rm app uv run --no-sync alembic upgrade head
dc up -d --force-recreate app nginx
dc ps
```

只重启应用：`dc restart app nginx`。只重启 PostgreSQL 或 Redis 前要确认业务影响；Redis 重启会使
内存中的连接短暂中断，AOF 可恢复已写入数据，但过期 Refresh Session 仍可能失效。

## 8. 备份和恢复底线

- PostgreSQL：至少每日逻辑备份（`pg_dump`），并把备份复制到 ECS 之外；重要变更前再做一次。
- Redis：备份 AOF/数据目录，但它主要保存 Refresh Session、验证码和 OAuth state；丢失后用户需
  重新登录，不应把 Redis 当成文章主数据备份。
- `/opt/fastapi-blog/data/media`：与 PostgreSQL 同时备份，否则数据库记录会指向不存在的图片。
- `/opt/fastapi-blog/config/app.env` 和 `compose-prod.env`：加密保存一份离线副本。

示例（先确认空间和目标路径）：

```bash
mkdir -p /opt/fastapi-blog/backups
dc exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | gzip > /opt/fastapi-blog/backups/fastapi_blog_$(date +%F).sql.gz
sudo tar -czf /opt/fastapi-blog/backups/media_$(date +%F).tar.gz \
  -C /opt/fastapi-blog/data media
```

执行备份命令时不要把密码写进命令行；使用容器内 `.pgpass`、临时受限环境变量或交互式输入。恢复
必须在隔离目录/数据库先演练，确认备份可读后再考虑生产恢复。

## 9. 安全和禁用操作

生产 ECS 安全组只开放 22、80、443，且 22 仅允许管理员 IP。禁止把 PostgreSQL、Redis 或 FastAPI
端口映射到公网。配置文件权限建议：

```bash
sudo chown root:root /opt/fastapi-blog/config/*.env
sudo chmod 640 /opt/fastapi-blog/config/*.env
```

不要执行：

- `docker compose down -v`（会删除 Compose 卷；即使当前使用 bind mount 也容易误删其他资源）。
- `docker system prune -a --volumes`（会删除回滚镜像和卷）。
- 未备份、未审查的 `alembic downgrade`、`DROP DATABASE` 或手工删表。
- 把 `DATABASE_URL`、Redis 密码、JWT Secret、邮箱授权码或私钥贴到日志和聊天。

## 10. 与 RDS/Tair 文档的关系

本机 Docker 方案不需要购买 RDS/Tair，也不应把 `DATABASE_URL`/`REDIS_URL` 指向云托管地址。旧的
RDS/Tair 说明仅用于迁移方案参考；当前正式 Compose 已以内置 `postgres`、`redis` 服务为准。
如果未来迁移回 RDS/Tair，应先备份并停止本机依赖，再改回托管地址，不能两套服务同时接收流量。
