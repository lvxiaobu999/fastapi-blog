# Redis 与 FastAPI 连接学习笔记

本文面向第一次使用 Redis 的开发者，说明当前 FastAPI 博客如何连接 Docker 中的 Redis，以及代码、容器和网络之间的关系。

## 1. 先理解 Redis 在项目中的位置

Redis 是一个独立运行的内存数据服务，不是 Python 变量，也不是 PostgreSQL/SQLite 的替代品。FastAPI 通过 TCP 连接 Redis，然后发送 `GET`、`SET`、`INCR`、`PUBLISH` 等命令。

```text
浏览器
  -> HTTP / WebSocket
FastAPI
  -> redis-py 异步客户端
  -> TCP 连接（默认端口 6379）
Redis 容器
  -> 内存数据，可选写入 Docker Volume
```

适合 Redis 的数据：

- 有过期时间的缓存，例如热门文章列表缓存 60 秒。
- 登录验证码、一次性令牌、限流计数器。
- WebSocket 多进程或多实例之间的 Pub/Sub 消息转发。
- 可以重新生成、短暂丢失不会破坏核心业务的数据。

不应只放 Redis 的数据：

- 用户、文章、评论、点赞、收藏等需要长期可靠保存的业务数据。
- 依赖复杂关联查询、外键和事务约束的数据。
- 丢失后无法恢复的唯一数据。

## 2. 当前仓库状态

当前仓库尚未接入 Redis：

- 没有 `compose.yaml` 或 `docker-compose.yml`。
- `pyproject.toml` 尚未安装 Python `redis` 客户端。
- `Settings` 尚未声明 `redis_url`。
- `app/main.py` 尚未在生命周期中创建和关闭 Redis 连接池。

所以下面的代码是下一步接入模板，不代表当前应用已经在使用 Redis。

## 3. 最容易混淆的连接地址

### FastAPI 在 Windows 本机，Redis 在 Docker

容器把 Redis 的 6379 端口映射到本机后，FastAPI 应连接：

```dotenv
REDIS_URL=redis://localhost:6379/0
```

这里的 `localhost` 是运行 FastAPI 的 Windows 主机。

### FastAPI 和 Redis 都在同一个 Compose

FastAPI 容器不能用 `localhost` 找 Redis，因为容器里的 `localhost` 只表示当前 FastAPI 容器。它应使用 Compose 服务名：

```dotenv
REDIS_URL=redis://redis:6379/0
```

其中 `redis` 来自 `services.redis`。同一个 Compose 项目的服务默认加入同一网络，Docker DNS 会把服务名解析成 Redis 容器地址。

```text
本机 FastAPI -> localhost:6379 -> 端口映射 -> Redis 容器
容器 FastAPI -> redis:6379     -> Compose 内部网络 -> Redis 容器
```

## 4. 只启动 Redis 的 Compose 示例

在项目根目录新增 `compose.yaml` 时，可以从下面开始：

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: fastapi-blog-redis
    restart: unless-stopped
    ports:
      # 仅供本机运行的 FastAPI 和调试工具访问。
      - "127.0.0.1:6379:6379"
    volumes:
      # Redis 重启后可恢复开启持久化时写入的数据。
      - redis_data:/data
    command: ["redis-server", "--appendonly", "yes"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  redis_data:
```

重要参数：

- `redis:7-alpine`：体积较小的 Redis 7 镜像；生产中建议固定到经过验证的具体小版本。
- `127.0.0.1:6379:6379`：只绑定本机回环地址，避免直接暴露给局域网或公网。
- `appendonly yes`：开启 AOF 持久化。它提高重启恢复能力，但 Redis 仍不应代替业务数据库。
- `redis_data`：Docker 管理的持久化卷。执行 `docker compose down -v` 会删除它，属于破坏性操作。
- `healthcheck`：`PONG` 说明 Redis 已经可以接受命令，不只是容器进程存在。

启动和检查：

```powershell
docker compose up -d redis
docker compose ps
docker compose logs redis
docker compose exec redis redis-cli ping
```

最后一条正常输出：

```text
PONG
```

## 5. 安装异步 Python 客户端

当前项目使用异步 FastAPI 请求链，因此使用 `redis.asyncio`：

```powershell
uv add redis
```

现代 `redis` 包已经包含异步客户端，不需要再安装旧的 `aioredis` 包。

## 6. Settings 配置示例

在 `Settings` 中增加：

```python
from pydantic import RedisDsn


class Settings(BaseSettings):
    # RedisDsn 会在应用启动时验证 URL 格式，避免到第一次请求才发现拼写错误。
    redis_url: RedisDsn = "redis://localhost:6379/0"
```

开发环境配置：

```dotenv
# FastAPI 在本机运行，Redis 端口从 Docker 映射到本机。
REDIS_URL=redis://localhost:6379/0
```

全容器环境配置：

```dotenv
# redis 是 Compose 服务名，不是随意命名的域名。
REDIS_URL=redis://redis:6379/0
```

URL 最后的 `/0` 表示 Redis 逻辑数据库编号 0。逻辑数据库共享同一个 Redis 实例的内存和资源，不能代替测试、开发、生产的实例隔离。

## 7. 创建 Redis 客户端

建议新增 `app/db/redis.py`，集中管理连接池：

```python
"""Redis 异步连接池及 FastAPI 依赖。"""

from collections.abc import AsyncIterator

from redis.asyncio import Redis

from app.core import get_settings

settings = get_settings()

# from_url 创建 Redis 客户端并在内部维护连接池。
# decode_responses=True 让 GET 返回 str，而不是初学者容易困惑的 bytes。
redis_client = Redis.from_url(
    str(settings.redis_url),
    encoding="utf-8",
    decode_responses=True,
    health_check_interval=30,
)


async def get_redis() -> AsyncIterator[Redis]:
    """把共享客户端注入 Router；每个请求不重复创建新连接池。"""

    yield redis_client
```

参数解释：

- `Redis.from_url(...)`：解析地址并创建客户端，真正命令会从内部连接池借连接。
- `decode_responses=True`：自动把 Redis 字节结果解码为字符串。
- `health_check_interval=30`：空闲连接再次使用前定期执行健康检查。
- 全局客户端不是“永远只用一条 TCP 连接”，它背后是可复用连接池。

## 8. 在 FastAPI 生命周期中检查和关闭

连接池应当在应用退出时关闭：

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.redis import redis_client
from app.db.session import engine


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 启动阶段主动 ping，让地址或认证错误尽早暴露，而不是等到第一个用户请求。
    await redis_client.ping()
    yield
    # 退出阶段释放 Redis 连接池和数据库 Engine。
    await redis_client.aclose()
    await engine.dispose()
```

执行顺序：

```text
Uvicorn 启动
  -> 创建 FastAPI lifespan
  -> Redis PING
  -> PONG 后开始接收请求
  -> 应用停止
  -> Redis aclose()
  -> SQLAlchemy engine.dispose()
```

如果 Redis 只是可选缓存，不希望 Redis 故障阻止整个博客启动，可以捕获明确的 `RedisError` 并降级；如果 Redis 承担会话、限流等必需功能，则启动失败通常比静默绕过更安全。

## 9. Router 中如何使用

```python
from typing import Annotated

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from app.db.redis import get_redis

RedisClient = Annotated[Redis, Depends(get_redis)]
router = APIRouter()


@router.get("/redis-health")
async def redis_health(redis: RedisClient) -> dict[str, bool]:
    """检查当前应用进程能否实际向 Redis 发送命令。"""

    return {"ok": bool(await redis.ping())}
```

FastAPI 的 `Depends` 只负责把共享客户端传给路由，不会为每个请求重新创建 Redis 实例。

## 10. 一个缓存示例

下面示例缓存文章详情 60 秒：

```python
import json


async def get_cached_post(redis: Redis, post_id: int) -> dict | None:
    key = f"post:{post_id}"
    cached = await redis.get(key)
    if cached is not None:
        return json.loads(cached)
    return None


async def cache_post(redis: Redis, post_id: int, data: dict) -> None:
    key = f"post:{post_id}"
    # ex=60 表示 60 秒后自动过期，避免旧缓存永久存在。
    await redis.set(key, json.dumps(data, ensure_ascii=False), ex=60)


async def invalidate_post_cache(redis: Redis, post_id: int) -> None:
    # 文章修改或删除后主动清理旧值。
    await redis.delete(f"post:{post_id}")
```

典型调用链：

```text
GET 文章
  -> Redis GET
  -> 命中：直接返回缓存
  -> 未命中：查询数据库 -> Redis SET EX 60 -> 返回

PATCH 文章
  -> 更新数据库并 commit
  -> Redis DEL
  -> 后续 GET 重新查询并生成缓存
```

数据库应当是真实数据来源。Redis 缓存删除失败最多导致短暂旧数据，不能让数据库事务与 Redis 写入假装成一个无法保证的跨系统事务。

## 11. WebSocket 与 Redis Pub/Sub

当前评论房间管理器只保存当前 Python 进程里的 WebSocket。如果以后启动多个 Uvicorn Worker：

```text
用户 A -> Worker 1 的 WebSocket 房间
用户 B -> Worker 2 的 WebSocket 房间
```

Worker 1 的内存管理器不知道 Worker 2 的连接。可以让每个 Worker 订阅 Redis 频道：

```text
评论写库成功
  -> Worker 1 PUBLISH comments:{post_id}
  -> Redis 把消息发给所有订阅者
  -> Worker 1 / Worker 2 各自收到消息
  -> 各自向本进程管理的 WebSocket 连接 broadcast
```

Pub/Sub 消息默认不持久化，订阅者离线时会错过消息。评论正文仍必须先写数据库，Redis 只负责跨进程实时通知；用户重连后通过 HTTP 历史接口补齐消息。

## 12. 密码和生产安全

本机学习环境可以只绑定 `127.0.0.1`。生产环境至少需要：

- 不把 6379 直接暴露到公网。
- 使用私有容器网络、安全组或防火墙限制来源。
- 使用 Redis ACL 用户和强密码，通过部署环境注入，不提交到 Git。
- 使用密码时的 URL 形式为 `redis://用户名:密码@主机:6379/0`；不要在日志中打印完整 URL。
- 跨不可信网络时使用 TLS，对应 `rediss://`。
- 设置内存上限和符合业务的淘汰策略，并监控连接数、内存、延迟和命中率。

## 13. 常用排错命令

```powershell
# 容器是否运行、健康检查是否通过
docker compose ps

# 查看启动、持久化和内存相关错误
docker compose logs redis

# 从 Redis 容器内部检查服务
docker compose exec redis redis-cli ping

# 检查某个键是否存在，不要在生产中执行 KEYS *
docker compose exec redis redis-cli EXISTS post:1

# 查看 TTL：-1 表示永不过期，-2 表示键不存在
docker compose exec redis redis-cli TTL post:1
```

常见错误：

| 现象 | 常见原因 | 检查方向 |
| --- | --- | --- |
| `Connection refused` | 容器没启动、端口没映射、地址写错 | `docker compose ps`、`ports`、URL |
| 容器 FastAPI 连不上 `localhost` | `localhost` 指向 FastAPI 容器自己 | 改为 Compose 服务名 `redis` |
| `Authentication required` | Redis 开启认证，URL 没有凭据 | ACL、Secret 注入、Redis URL |
| GET 得到 `b'value'` | 未开启自动解码 | `decode_responses=True` |
| 修改文章后仍看到旧内容 | 缓存未失效或 TTL 太长 | 写操作后 `DEL`、检查 TTL |
| 单 Worker 正常，多 Worker 推送丢失 | WebSocket 房间只在进程内存中 | Redis Pub/Sub 跨进程转发 |

## 14. 推荐接入顺序

1. 先用 Compose 只启动 Redis，并确认 `redis-cli ping` 返回 `PONG`。
2. 执行 `uv add redis`。
3. 为 `Settings` 增加 `redis_url`，开发环境使用 `localhost`。
4. 新增共享异步客户端和 `get_redis` 依赖。
5. 在 lifespan 中执行 `ping()` 和 `aclose()`。
6. 先写一个健康检查或最小缓存测试，确认连接链路。
7. 再选择具体业务接入缓存、限流或 WebSocket Pub/Sub。
8. 为 Redis 不可用、缓存命中、未命中、过期和失效行为增加测试。

Redis 不需要 Alembic Migration。Alembic 只管理关系型数据库表结构；Redis 的键名、TTL 和数据格式应通过代码约定、测试和文档维护。
