"""异步数据库 Engine、Session 工厂和 FastAPI 数据库依赖。

Engine 在模块导入时只完成配置，不会立即连接数据库；第一次执行 SQL 时才建立连接。
表结构由 Alembic 管理，本模块不会调用 ``Base.metadata.create_all()``。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import get_settings

settings = get_settings()

engine = create_async_engine(
    # 异步 Engine 要求异步驱动：开发使用 aiosqlite，生产使用 psycopg 异步实现。
    settings.database_url,
    # 从连接池取出连接前检查存活状态，减少数据库重启或连接超时后的首次请求失败。
    pool_pre_ping=True,
)

# async_sessionmaker 是 AsyncSession 工厂，每次调用才创建一个独立会话。
# autoflush=False：查询前不隐式 flush，写入时机由 Service 明确控制。
# expire_on_commit=False：提交后保留已加载属性，响应序列化不会隐式触发异步查询。
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """为一次请求提供 AsyncSession，并在请求结束后自动归还连接。"""

    async with AsyncSessionLocal() as session:
        yield session
