"""Redis 异步连接池与 FastAPI 依赖。

本模块只管理共享客户端的创建、注入和关闭，不编写 Refresh Session Key，也不包含认证
规则。redis-py 的异步客户端内部使用连接池，可以被多个请求安全复用，无需每次新建连接。
"""

from collections.abc import AsyncIterator

from redis.asyncio import Redis

from app.core import get_settings

settings = get_settings()

# decode_responses=True 让 GET/HGETALL 直接返回 str，避免认证 Service 到处手工解码 bytes。
# health_check_interval 会在空闲连接重新使用时检查连接，socket 超时保证 Redis 故障快速返回。
redis_client = Redis.from_url(
    settings.redis_url.get_secret_value(),
    decode_responses=True,
    socket_connect_timeout=settings.redis_socket_timeout_seconds,
    socket_timeout=settings.redis_socket_timeout_seconds,
    health_check_interval=30,
)


async def get_redis() -> AsyncIterator[Redis]:
    """向一次请求注入共享 Redis 客户端；客户端本身在应用生命周期结束时统一关闭。"""

    yield redis_client


async def close_redis() -> None:
    """应用退出时关闭连接池中的 Redis 连接，支持 FastAPI 优雅停机。"""

    await redis_client.aclose()
