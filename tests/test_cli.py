"""管理命令复用的管理员创建业务测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas.user import UserCreate
from app.services.auth import verify_password
from app.services.users import create_user

pytestmark = pytest.mark.anyio


async def test_create_admin_uses_hash_and_admin_flag(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """CLI 的可信参数能创建管理员，同时数据库中只保存密码哈希。"""

    async with session_factory() as session:
        user = await create_user(
            session,
            UserCreate(
                username="site_admin",
                email="admin@example.com",
                password="password123",
            ),
            is_admin=True,
        )

    assert user.is_admin is True
    assert user.hashed_password != "password123"
    assert await verify_password("password123", user.hashed_password)
