"""异步数据库与 HTTP 客户端共享测试配置。"""

from collections.abc import AsyncIterator

import pytest
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.db.redis import get_redis
from app.main import app
from app.models import Category


@pytest.fixture
def anyio_backend() -> str:
    """固定使用 asyncio，避免 AnyIO 同时尝试未安装的 Trio 后端。"""

    return "asyncio"


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """为每个测试创建共享连接的异步内存 SQLite 数据库。"""

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
async def seeded_categories(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    """写入帖子测试共用的分类，并按 slug 返回主键。"""

    async with session_factory() as session:
        categories = [
            Category(name="FastAPI", slug="fastapi", sort_order=10),
            Category(name="Python", slug="python", sort_order=20),
            Category(name="其它", slug="other", sort_order=30),
        ]
        session.add_all(categories)
        await session.commit()
        return {category.slug: category.id for category in categories}


@pytest.fixture
async def fake_redis() -> AsyncIterator[FakeRedis]:
    """为每个测试提供独立内存 Redis，并在用例结束后关闭连接。"""

    client = FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
) -> AsyncIterator[AsyncClient]:
    """覆盖真实数据库依赖，并通过 ASGI 异步调用 FastAPI。"""

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_get_redis() -> AsyncIterator[FakeRedis]:
        """每个测试客户端使用独立内存 Redis，避免依赖本机服务或共享历史会话。"""

        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_redis, None)
