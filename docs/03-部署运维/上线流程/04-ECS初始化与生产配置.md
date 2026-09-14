# ECS 初始化与生产配置

本章在 ECS 上执行。它只准备运行环境和 Secret，不迁移数据库、不开放正式 DNS。所有命令默认
使用 Linux shell。

## 1. 创建部署用户

首次可通过阿里云控制台/云助手或受控管理员账号登录，然后创建专用用户。不同发行版命令可能
不同，Ubuntu 示例：

```bash
sudo adduser deploy
sudo usermod -aG sudo deploy
```

为 `deploy` 配置 SSH 公钥，验证新会话可以登录后，再关闭旧的高权限会话。不要把私钥上传到
服务器或 Git。

## 2. 安装基础工具和 Docker

先更新系统并安装基础工具：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git netcat-openbsd openssl
```

Docker Engine/Compose 插件的仓库配置随发行版和时间变化，应按照 Docker 官方或阿里云针对当前
ECS 系统的安装文档完成，不使用来源不明的 `curl | sh` 脚本。

安装后验证：

```bash
docker version
docker compose version
sudo systemctl enable --now docker
sudo systemctl status docker --no-pager
```

如果把 `deploy` 加入 `docker` 组：

```bash
sudo usermod -aG docker deploy
```

需要重新登录才生效。能访问 Docker Socket 通常等同拥有 root 权限，`docker` 组不是低权限
沙箱，只能加入受信任部署人员。

## 3. 建立部署目录

```bash
sudo install -d -m 755 -o deploy -g deploy /opt/fastapi-blog/current
sudo install -d -m 750 -o root -g docker /opt/fastapi-blog/config
sudo install -d -m 750 -o root -g docker /opt/fastapi-blog/secrets/tls
sudo install -d -m 755 -o 10001 -g 10001 /opt/fastapi-blog/data/media
```

目录职责：

```text
/opt/fastapi-blog/current       已测试的源码 tag/commit
/opt/fastapi-blog/config        生产 app.env
/opt/fastapi-blog/secrets/tls   fullchain.pem 和 privkey.key
/opt/fastapi-blog/data/media    容器 UID 10001 可写的上传目录
```

如果 `/opt` 位于独立数据盘，先确认 `findmnt /opt` 和 `/etc/fstab` 挂载正常。

## 4. 拉取发布版本

使用只读 Deploy Key 配置仓库访问，然后：

```bash
git clone <YOUR_REPOSITORY_URL> /opt/fastapi-blog/current
cd /opt/fastapi-blog/current
git fetch --tags --prune
git checkout <RELEASE_TAG>
git status --short
git rev-parse --short HEAD
```

预期 `git status --short` 没有输出。生产服务器不编辑源码；紧急修改也应回到开发分支测试、提交，
再按相同发布流程进入生产。

## 5. 创建生产 app.env

先创建受限文件：

```bash
sudo touch /opt/fastapi-blog/config/app.env
sudo chown root:docker /opt/fastapi-blog/config/app.env
sudo chmod 640 /opt/fastapi-blog/config/app.env
sudoedit /opt/fastapi-blog/config/app.env
```

`touch` 在文件已经存在时只更新时间，不会清空内容；因此这组权限命令可以重复执行。不要用把
`/dev/null` 安装到目标路径的方式处理已有 `app.env`，否则可能把生产配置截断成空文件。

模板如下，尖括号必须替换：

```dotenv
# 决定 Settings 启用生产校验；缺失时镜像内没有 .env，会默认成 development 并启动失败。
ENV=production
PROJECT_TITLE=三碗博客

# JSON 数组，只写主机名，不带 https://、端口或路径；第一个值供 Compose readiness 使用。
ALLOWED_HOSTS=["<APP_DOMAIN>"]

# DATABASE_URL 由 compose.production.yaml 根据 compose-prod.env 自动生成；不要在此文件重复填写。

# 使用安全随机值，不复用开发密钥；轮换会使现有 Access JWT 失效。
SECRET_KEY=<RANDOM_SECRET>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_MINUTES=10080
REFRESH_IDLE_TIMEOUT_MINUTES=1440

# 生产强制 true，否则 Settings 拒绝启动。
AUTH_COOKIE_SECURE=true

# REDIS_URL 由 compose.production.yaml 根据 compose-prod.env 自动生成；不要在此文件重复填写。
REDIS_KEY_PREFIX=fastapi-blog:production
REDIS_SOCKET_TIMEOUT_SECONDS=2

# 容器生产写 stdout JSON，由 Docker/SLS 负责采集和保留。
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_TO_FILE=false
LOG_DIRECTORY=logs
LOG_MAX_BYTES=52428800
LOG_FILES_PER_DAY=5
LOG_RETENTION_DAYS=30
```

### 5.1 关键组合校验

当前 `Settings` 会拒绝：

- `ENV=production` 但数据库不是 `postgresql+psycopg://`。
- 生产 Redis 指向 `localhost/127.0.0.1/::1`。
- `AUTH_COOKIE_SECURE=false`。
- `SECRET_KEY` 少于 32 个字符。
- `ALLOWED_HOSTS` 包含 `*`、协议、路径、空格、`test` 或 `testserver`。
- Refresh 空闲时间不大于 Access Token 时间。
- Redis Key 前缀包含 `{` 或 `}`。

### 5.2 生成 Secret

在受控终端生成：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

输出只写入 `app.env`，不要保存到 shell 脚本、聊天或工单。编辑后检查权限，不打印内容：

```bash
sudo stat -c '%a %U %G %n' /opt/fastapi-blog/config/app.env
```

预期类似 `640 root docker`。

## 6. 配置 Compose 参数

### 6.0 使用独立的 `compose-prod.env`（推荐）

为了让 Docker 参数和 FastAPI 运行时配置一眼可区分，仓库提供了
[`deploy/compose-prod.env.example`](../../../deploy/compose-prod.env.example)。请复制为本地未提交的
`deploy/compose-prod.env`；由于当前 PostgreSQL/Redis 也由 Docker
运行，它还保存两个容器的启动账号和密码；上传到 ECS 后建议限制为 root:docker、640：

```bash
# 在本地仓库执行；目标目录先不要直接覆盖正在使用的文件。
scp deploy/compose-prod.env <ECS用户>@<ECS公网IP>:/tmp/compose-prod.env

# 在 ECS 执行，确认内容后再安装到配置目录。
sudo install -o root -g docker -m 640 /tmp/compose-prod.env \
  /opt/fastapi-blog/config/compose-prod.env
```

然后在 ECS 每次发布时执行：

```bash
cd /opt/fastapi-blog/current
export COMPOSE_FILE=compose.production.yaml
export COMPOSE_ENV=/opt/fastapi-blog/config/compose-prod.env

# 用当前已测试提交覆盖文件中的默认标签，保证代码、容器和发布记录对应。
export APP_IMAGE_TAG=$(git rev-parse --short HEAD)

test -r "$COMPOSE_ENV"
test -r /opt/fastapi-blog/config/app.env

# -q 只做 YAML/变量解析检查，不输出展开后的配置。
docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" config -q
# --pull 尝试更新基础镜像；build 会把当前提交的 app/static 一并复制进新镜像。
docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" build --pull app

# 确认 PostgreSQL 备份和待执行 Migration 后再写入数据库。
docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" up -d postgres redis
docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" run --rm app uv run --no-sync alembic upgrade head
docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" up -d --force-recreate
docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" ps
```

`compose-prod.env` 中的 `APP_ENV_FILE` 会让 Compose 在启动 `app` 时读取
`/opt/fastapi-blog/config/app.env`；两个文件职责不同，不能互相替代。上面导出的
`APP_IMAGE_TAG` 会覆盖文件中的 `production` 默认值；如果不覆盖，也必须先 build 再执行
`up -d --force-recreate`，否则可能继续运行旧容器。

上面命令中的反斜杠（`\\`）如果用于换行，必须是该行最后一个字符，后面不能有空格；也可以直接复制为一行执行。

`--env-file` 和 Compose 服务中的 `env_file` 不是同一件事：

| 写法 | 读取时机 | 在当前项目中的作用 | 是否替代 `app.env` |
|---|---|---|---|
| `docker compose --env-file <文件>` | Compose 解析 YAML 之前 | 提供 `APP_DOMAIN`、`APP_IMAGE_TAG`、`POSTGRES_*`、`REDIS_PASSWORD` 等 `${...}` 插值值 | 否；只影响 Compose 解析 |
| `services.app.env_file: <文件>` | 创建/启动 `app` 容器时 | 将 `ENV`、`SECRET_KEY` 等注入 FastAPI；连接串由 `environment` 覆盖注入 | 是应用真正读取的运行时配置 |

当前 Compose 会从 `compose-prod.env` 读取 PostgreSQL/Redis 启动密码并自动生成 app 连接串，不能再
把 `app.env` 临时当作 Compose 的 `--env-file`。缺少 `compose-prod.env` 时应先从模板复制并填写密码，
再执行 `config -q`；不要为了绕过缺失文件把 Secret 作为命令行参数输入。

### 6.0.1 原命令逐段解释

你遇到的命令可以拆成下面几部分。反斜杠（`\\`）只是 Bash 的续行符，必须是该行最后一个字符，
后面不能再有空格；在 Windows PowerShell 中不要照抄反斜杠续行，改为一行执行或使用反引号。

| 片段 | 作用 | 结果 |
|---|---|---|
| `docker compose` | 调用 Docker Compose v2 | 读取 Compose 文件并管理服务、镜像和网络 |
| `--env-file /opt/fastapi-blog/config/compose-prod.env` | 读取 **Compose 插值变量** | 为 YAML 中的 `${APP_DOMAIN}`、`${APP_IMAGE_TAG}` 等提供值；文件不存在会立即报错 |
| `-f compose.production.yaml` | 指定生产编排文件 | 启动 `app`、`nginx`、`postgres`、`redis` 四个服务 |
| `build` | 根据 Dockerfile 构建镜像 | 重新打包当前提交的代码、模板和 `app/static` |
| `--pull` | 构建前尝试拉取更新的基础镜像 | 可获得最新安全修复；需要 ECS 能访问镜像仓库 |
| `app` | 只构建 `app` 服务 | 不会启动容器，也不会执行数据库迁移 |

因此，`build --pull app` 解决的是“生成新应用镜像”，不是启动服务。构建完成后还需按顺序执行
`run --rm app uv run --no-sync alembic upgrade head`（确认迁移后）和 `up -d`。如果只执行 `up -d`，
Compose 可能继续复用旧镜像，浏览器仍会看到旧 CSS、Logo 或 favicon。

### 6.1 发布前检查 `app.env` 是否适用于正式 Compose

如果 `app.env` 是从本地 `.env` 直接复制来的，请先确认它不是开发配置。正式的
`compose.production.yaml` 已提供名为 `postgres`、`redis` 的内部服务；因此 `DATABASE_URL` 和
`REDIS_URL` 必须分别使用 `postgres:5432`、`redis:6379`。不需要叠加
`compose.public-ip.local-services.yaml`，该文件仅为历史临时方案保留。

不打印密码的检查方式：

```bash
test -r /opt/fastapi-blog/config/app.env
grep -E '^(ENV|ALLOWED_HOSTS|APP_DOMAIN|AUTH_COOKIE_SECURE|PUBLIC_IP_MODE|LOG_LEVEL|LOG_FORMAT)=' \
  /opt/fastapi-blog/config/app.env
```

预期至少包含 `ENV=production`、`ALLOWED_HOSTS=["www.sanwan.xyz"]`、
`AUTH_COOKIE_SECURE=true` 和 `PUBLIC_IP_MODE=false`。`APP_DOMAIN` 应在 `compose-prod.env` 中维护。数据库 URI、
Redis URI、`SECRET_KEY`、SMTP 授权码和 QQ Client Secret 只检查“是否存在”，不要直接打印内容。

这里有两层容易混淆的配置：

1. 下面这组 `APP_*` 和 `MEDIA_HOST_PATH`、`TLS_HOST_PATH` 是 **Docker Compose 插值参数**。它们由
   Compose 在读取 `compose.production.yaml` 时使用，决定域名、镜像标签和宿主机目录；它们不是
   FastAPI 的业务配置。
2. `/opt/fastapi-blog/config/app.env` 是 **应用运行时配置**。Compose 会把其中的变量注入 `app`
   容器，FastAPI 的 Settings 再读取 `ENV`、`SECRET_KEY` 等值；`DATABASE_URL`、`REDIS_URL` 由
   Compose 根据 `compose-prod.env` 生成并覆盖。数据库密码、Redis 密码和 JWT Secret 不要放进
   下面的 `export` 命令。

每次登录服务器或打开新的 shell 后，都要重新设置 Compose 参数：

```bash
cd /opt/fastapi-blog/current
export COMPOSE_ENV=/opt/fastapi-blog/config/compose-prod.env
export APP_IMAGE_TAG=$(git rev-parse --short HEAD)
test -r "$COMPOSE_ENV"
```

参数逐项说明：

| 参数 | Compose 中的用途 | 为什么需要它 | 改错或不设置的结果 |
|---|---|---|---|
| `APP_DOMAIN` | 替换 Nginx 模板中的 `${APP_DOMAIN}`，生成 `server_name`、HTTP 到 HTTPS 的跳转地址和转发时的 `Host` | 让浏览器访问的域名、Nginx、证书和 FastAPI `ALLOWED_HOSTS` 使用同一个正式入口 | 未设置时 Compose 直接报错；写成带 `https://`、端口或错误域名时会出现证书不匹配、跳转错误或 `400 Invalid Host` |
| `APP_IMAGE_TAG` | 指定应用镜像名 `fastapi-blog:<标签>` | 把正在运行的容器和已测试的 Git commit/tag 对应起来，便于发布记录和回滚 | 不更新可能继续运行旧镜像；使用含糊的 `latest` 会难以确认线上实际版本 |
| `APP_ENV_FILE` | 指定 Compose 读取并注入 `app` 容器的环境文件 | 将生产数据库、Redis、JWT 和 Cookie 配置放在仓库外的受限文件中 | 路径错误或文件缺失时，应用可能因缺少生产配置而无法启动；不要把它改成仓库中的开发 `.env` |
| `MEDIA_HOST_PATH` | 将宿主机目录绑定到容器 `/app/app/media` | 用户头像和文章图片必须存放在容器外，重建容器后才能保留 | 目录不存在或 UID 10001 无写权限时，上传失败或重建后文件看似丢失 |
| `TLS_HOST_PATH` | 将宿主机证书目录只读绑定到 Nginx `/etc/nginx/tls` | Nginx 需要从固定位置读取 `fullchain.pem` 和 `privkey.key`，FastAPI 本身不终止 TLS | 路径错误或文件缺失时 Nginx `nginx -t` 失败，443 无法启动；私钥不能提交 Git |

其中 `APP_DOMAIN`、证书中的域名以及 `app.env` 的 `ALLOWED_HOSTS` 必须一致；`APP_IMAGE_TAG`
应来自当前已测试版本，例如 `git rev-parse --short HEAD`；两个目录参数必须是 **ECS 宿主机**
上的绝对路径，而不是容器内路径。

可以用下面的命令只检查非敏感参数是否存在，不会打印 `app.env` 内容：

```bash
grep -E '^(APP_DOMAIN|APP_IMAGE_TAG|APP_ENV_FILE|MEDIA_HOST_PATH|TLS_HOST_PATH)=' "$COMPOSE_ENV"
docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml config -q
```

后续所有 Compose 命令必须在这些变量存在的同一 shell 执行。shell 关闭后 `export` 会失效，
下一次登录需要重新设置；也可以把这五个非敏感参数放入受控部署脚本，但每次发布仍应更新
`APP_IMAGE_TAG`。不要使用 `docker compose config`（不带 `-q`）把展开后的配置完整输出到日志，
因为其中可能包含 `env_file` 相关的敏感信息。

## 7. 准备 TLS 文件

当前 [Nginx 模板](../../../nginx/default.conf.template) 固定读取：

```text
/etc/nginx/tls/fullchain.pem
/etc/nginx/tls/privkey.key
```

宿主机必须提供：

```text
/opt/fastapi-blog/secrets/tls/fullchain.pem
/opt/fastapi-blog/secrets/tls/privkey.key
```

第一次推荐在阿里云 SSL 证书服务通过 DNS 验证签发，再下载 Nginx 格式证书并放入该目录。
当前 Compose 没有 Certbot challenge 目录，且证书缺失时 Nginx 无法启动；使用 HTTP-01 需要先
另行准备临时纯 HTTP 配置，不能假设当前模板会自动申请证书。

阿里云下载的 Nginx 证书文件名可能不是这两个名称；复制到部署目录时应明确重命名，不能改动
文件内容。权限检查：

```bash
sudo chown root:docker /opt/fastapi-blog/secrets/tls/fullchain.pem
sudo chown root:docker /opt/fastapi-blog/secrets/tls/privkey.key
sudo chmod 640 /opt/fastapi-blog/secrets/tls/privkey.key
sudo chmod 644 /opt/fastapi-blog/secrets/tls/fullchain.pem
sudo ls -l /opt/fastapi-blog/secrets/tls
```

不能输出 `privkey.key` 内容。

## 8. 检查 media 权限

镜像中的应用用户 UID 是 10001：

```bash
sudo stat -c '%a %u %g %n' /opt/fastapi-blog/data/media
```

预期 owner UID/GID 能让 10001 写入。容器启动后还会做实际写入验证；不要为了省事使用 `777`。

## 9. 构建前 Compose 预检

```bash
cd /opt/fastapi-blog/current
docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml config -q
```

没有输出且退出码为 0 表示 Compose 语法和变量解析通过。它不会连接 PostgreSQL/Redis，也不会检查证书
内容。

构建应用：

```bash
docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml build --pull app
docker image inspect "fastapi-blog:${APP_IMAGE_TAG}" --format '{{.Id}} {{.Created}}'
```

## 10. 只验证 Settings，不打印 Secret

```bash
docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml run --rm app \
  uv run --no-sync python -c "from app.core import get_settings; s=get_settings(); print(s.env, s.project_title, s.allowed_hosts)"
```

预期输出 production、项目标题和正式域名数组。此命令只实例化 Settings，不主动连接数据库或
Redis。失败时阅读 Pydantic 最后一条校验错误，不要打印完整 Settings。

## 11. 检查 Nginx 模板和证书

```bash
docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml run --rm --no-deps nginx nginx -t
```

Nginx 官方镜像 entrypoint 会先把 `${APP_DOMAIN}` 替换进模板，再检查生成配置。预期包含：

```text
syntax is ok
test is successful
```

失败时检查域名、证书路径/格式和 Nginx 配置；不要用跳过 `nginx -t` 的方式强行启动。

## 12. 本阶段完成标准

- [ ] 部署用户、SSH 和 Docker 权限已验证。
- [ ] 生产源码是明确 tag/commit，工作树干净。
- [ ] `app.env` 权限受限且不在仓库目录。
- [ ] Settings 生产组合校验通过，没有打印 Secret。
- [ ] TLS 文件存在，私钥权限正确，`nginx -t` 通过。
- [ ] media 目录由容器 UID 10001 可写。
- [ ] Compose `config -q` 和 app 镜像构建通过。
- [ ] 尚未执行生产数据库 Migration。

下一步进入 [05-首次部署与数据库迁移](05-首次部署与数据库迁移.md)。
