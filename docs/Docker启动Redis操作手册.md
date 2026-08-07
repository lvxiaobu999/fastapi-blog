# Docker 启动 Redis 操作手册

## 1. FastAPI 会自动启动 Redis 吗

不会。下面三个组件是独立进程：

```text
Docker Desktop
  -> 提供 Docker Engine

Redis 容器
  -> 由 docker compose 启动

FastAPI
  -> 根据 REDIS_URL 连接已经运行的 Redis
```

`REDIS_URL` 只是连接地址，不是启动命令。如果 Redis 没有运行：

- 公开页面和不依赖 Redis 的读取接口仍可以工作。
- 已有有效 Access JWT 的普通认证请求不访问 Redis。
- 登录、Refresh、退出和改密等 Redis 会话操作返回 `503`。

## 2. 当前仓库已经配置什么

本地 Redis 配置文件：

```text
compose.redis.development.yaml
```

它包含：

- 固定镜像 `redis:7.4.2-alpine`。
- 只绑定 `127.0.0.1:6379`，不会直接暴露给局域网或公网。
- AOF 持久化，采用 `appendfsync everysec`。
- Docker Volume 保存 `/data`。
- `redis-cli ping` 健康检查。
- `restart: unless-stopped` 自动恢复策略。

FastAPI 开发环境配置：

```dotenv
REDIS_URL=redis://localhost:6379/0
REDIS_KEY_PREFIX=fastapi-blog:development
REDIS_SOCKET_TIMEOUT_SECONDS=2
```

当前结构是“FastAPI 在 Windows 本机，Redis 在 Docker 容器”，所以地址使用 `localhost`。只有 FastAPI 也进入 Compose 网络时，才把主机名改为 Redis 服务名 `redis`。

## 3. 首次启动

### 第一步：启动 Docker Desktop

先打开 Docker Desktop，等待界面显示 Docker Engine 已运行。PowerShell 验证：

```powershell
docker version
```

必须同时看到 Client 和 Server 信息。如果出现下面内容，说明 Docker Desktop 或 Linux Engine 尚未启动：

```text
failed to connect to the docker API
dockerDesktopLinuxEngine ... file not found
```

### 第二步：启动 Redis 容器

在项目根目录执行：

```powershell
Set-Location D:\study\python\fastapi-blog
docker compose -f compose.redis.development.yaml up -d
```

命令第一次运行会下载 Redis 镜像，之后通常直接复用本地镜像。`-d` 表示后台运行，不占用当前终端。

### 第三步：检查容器和健康状态

```powershell
docker compose -f compose.redis.development.yaml ps
```

期望看到 Redis 为 `running` 或 `healthy`。直接执行 PING：

```powershell
docker compose -f compose.redis.development.yaml exec redis redis-cli ping
```

期望输出：

```text
PONG
```

### 第四步：启动 FastAPI

Redis 健康后再启动项目：

```powershell
$env:ENV = "development"
uv run fastapi dev app/main.py
```

启动 FastAPI 不会重复创建 Redis 容器；它只建立连接池，并在登录或刷新时使用连接。

## 4. 每天开发怎么操作

Docker Desktop 已运行时，下面命令是幂等的：容器不存在就创建，已经运行就保持运行。

```powershell
docker compose -f compose.redis.development.yaml up -d
```

推荐顺序：

```text
启动 Docker Desktop
  -> docker compose up -d
  -> redis-cli ping 得到 PONG
  -> 启动 FastAPI
```

如果在 Docker Desktop 设置中启用了“登录 Windows 后启动 Docker Desktop”，并且 Redis 容器没有被手动 stop，`restart: unless-stopped` 会在 Docker Engine 重启后尝试恢复 Redis。但 FastAPI 本身仍不会负责启动 Docker Desktop。

## 5. 启动、停止与删除的区别

### 临时停止 Redis

```powershell
docker compose -f compose.redis.development.yaml stop redis
```

容器保留，Volume 保留。恢复：

```powershell
docker compose -f compose.redis.development.yaml start redis
```

### 删除容器但保留数据

```powershell
docker compose -f compose.redis.development.yaml down
```

这会删除容器和 Compose 网络，但默认保留命名 Volume。以后执行 `up -d` 会重新创建容器并挂载原 Volume。

### 不要随意删除 Volume

下面命令会删除 Redis Volume 和其中所有 Refresh Session/AOF 数据，属于破坏性操作：

```powershell
docker compose -f compose.redis.development.yaml down -v
```

本项目正常开发不需要执行 `down -v`。如果确实删除，所有 Redis 登录会话都会丢失，用户必须重新登录。

## 6. 查看日志和 Redis 数据

查看容器日志：

```powershell
docker compose -f compose.redis.development.yaml logs --tail 100 redis
```

持续查看：

```powershell
docker compose -f compose.redis.development.yaml logs -f redis
```

进入 Redis CLI：

```powershell
docker compose -f compose.redis.development.yaml exec redis redis-cli
```

常用只读命令：

```text
PING
INFO server
INFO memory
DBSIZE
SCAN 0 MATCH fastapi-blog:development:* COUNT 100
TTL <完整Key>
GET <RefreshSession完整Key>
```

不要在生产环境使用 `KEYS *`，它会阻塞 Redis 扫描全部 Key。使用渐进式 `SCAN`。

Refresh Token 原文不会出现在 Redis Key 或 Value 中，Key 使用 SHA-256 摘要。不要把完整 Value、Cookie 或带密码的 Redis URL复制到日志和聊天记录。

## 7. 数据保存在哪里

Compose 使用命名 Volume：

```text
fastapi-blog_fastapi_blog_redis_data
```

查看：

```powershell
docker volume inspect fastapi-blog_fastapi_blog_redis_data
```

Redis 在 `/data` 中保存 AOF。Volume 不属于 Git，也不应复制到仓库。Refresh Session 本身带 TTL，到期后由 Redis 自动删除。

## 8. 常见故障

### Docker API 连接失败

```text
dockerDesktopLinuxEngine ... file not found
```

原因：Docker Desktop 没启动，或 Linux Engine 尚未就绪。先启动 Docker Desktop，再运行 `docker version`。

### 6379 端口被占用

```powershell
Get-NetTCPConnection -LocalPort 6379 -ErrorAction SilentlyContinue
```

可能已有本机 Redis 或其他容器。不要随意结束未知进程；确认占用来源后，选择停止旧服务或修改 Compose 端口与 `REDIS_URL`。

### FastAPI 登录返回 503

依次检查：

```powershell
docker compose -f compose.redis.development.yaml ps
docker compose -f compose.redis.development.yaml exec redis redis-cli ping
Get-ChildItem Env:REDIS_URL
```

系统环境变量优先于 `.env.development`，旧的 `REDIS_URL` 可能覆盖文件配置。清除临时覆盖：

```powershell
Remove-Item Env:REDIS_URL -ErrorAction SilentlyContinue
```

然后重启 FastAPI。

### 容器不断重启

```powershell
docker compose -f compose.redis.development.yaml logs --tail 200 redis
```

重点查看 AOF 损坏、Volume 权限、内存不足和命令参数错误。不要在未备份时删除 Volume。

## 9. 更新 Redis 镜像

当前固定到 `redis:7.4.2-alpine`，避免每次启动意外获得不同版本。升级前先阅读 Redis Release Notes，在开发环境验证认证 Lua 脚本和 AOF：

```powershell
docker compose -f compose.redis.development.yaml pull redis
docker compose -f compose.redis.development.yaml up -d redis
```

不要在生产中直接使用浮动的 `latest` 标签。

## 10. 生产环境不能直接照搬

本地 Compose 没有密码，只因端口限制在 `127.0.0.1`。生产环境至少需要：

- Redis ACL 或强密码，并由 Secret 管理。
- 禁止将 6379 暴露到公网。
- 跨不可信网络使用 TLS，即 `rediss://`。
- AOF、备份、主从/Sentinel 或托管高可用。
- 内存、连接数、延迟、拒绝连接和复制状态监控。
- 明确 `maxmemory` 与淘汰策略；认证会话不能被普通缓存随意挤掉。

生产通常使用托管 Redis，或把 FastAPI 和 Redis 放到内部容器网络中；此时 `REDIS_URL` 由部署平台注入，不写入仓库。

## 11. 快速命令表

```powershell
# 启动
docker compose -f compose.redis.development.yaml up -d

# 状态
docker compose -f compose.redis.development.yaml ps

# 健康检查
docker compose -f compose.redis.development.yaml exec redis redis-cli ping

# 日志
docker compose -f compose.redis.development.yaml logs --tail 100 redis

# 停止但保留容器
docker compose -f compose.redis.development.yaml stop redis

# 恢复已停止容器
docker compose -f compose.redis.development.yaml start redis

# 删除容器但保留 Volume
docker compose -f compose.redis.development.yaml down
```
