"""JWT 登录、身份校验和发帖授权测试。"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.services.auth import (
    create_access_token,
    hash_password,
    verify_access_token,
    verify_password,
)

pytestmark = pytest.mark.anyio


async def register(client: AsyncClient, username: str = "alice") -> dict:
    """通过公开注册接口创建具备真实 Argon2 密码的测试用户。"""

    response = await client.post(
        "/api/users",
        json={"username": username, "email": f"{username}@example.com", "password": "password123"},
    )
    assert response.status_code == 201
    return response.json()


async def login(client: AsyncClient, username: str = "alice", password: str = "password123"):
    """调用 OAuth2 表单登录接口。"""

    return await client.post("/api/auth/token", data={"username": username, "password": password})


async def test_explicit_password_and_token_helpers() -> None:
    """四个显式安全函数分别完成哈希、校验、签发和验证。"""

    hashed = await hash_password("password123")
    assert hashed != "password123"
    assert await verify_password("password123", hashed) is True
    assert await verify_password("wrong-password", hashed) is False

    token = create_access_token(42)
    assert verify_access_token(token) == 42


async def test_login_returns_bearer_token_and_rejects_bad_credentials(client: AsyncClient) -> None:
    await register(client)
    success = await login(client)
    wrong_password = await login(client, password="wrong-password")
    missing_user = await login(client, username="nobody")

    assert success.status_code == 200
    assert success.json()["token_type"] == "bearer"
    assert success.json()["access_token"]
    assert wrong_password.status_code == missing_user.status_code == 401
    assert wrong_password.json() == missing_user.json()
    assert wrong_password.headers["www-authenticate"] == "Bearer"


async def test_create_post_requires_valid_admin_token(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await register(client)
    token = (await login(client)).json()["access_token"]
    payload = {"title": "JWT protected", "content": "Only administrators publish."}

    missing = await client.post("/api/posts", json=payload)
    regular = await client.post(
        "/api/posts", json=payload, headers={"Authorization": f"Bearer {token}"}
    )
    async with session_factory() as session:
        stored_user = await session.get(User, user["id"])
        assert stored_user is not None
        stored_user.is_admin = True
        await session.commit()
    admin = await client.post(
        "/api/posts", json=payload, headers={"Authorization": f"Bearer {token}"}
    )

    assert missing.status_code == 401
    assert regular.status_code == 403
    assert admin.status_code == 201
    assert admin.json()["user_id"] == user["id"]


async def test_expired_and_forged_tokens_return_401(client: AsyncClient) -> None:
    user = await register(client)
    expired = create_access_token(user["id"], expires_delta=timedelta(seconds=-1))
    for token in (expired, "not-a-jwt"):
        response = await client.post(
            "/api/posts",
            json={"title": "Denied", "content": "Denied"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
