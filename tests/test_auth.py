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
    return response.json()["data"]


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
    success = await login(client, username=" ALICE ")
    wrong_password = await login(client, password="wrong-password")
    missing_user = await login(client, username="nobody")

    assert success.status_code == 200
    assert success.json()["data"]["token_type"] == "bearer"
    assert success.json()["data"]["access_token"]
    assert wrong_password.status_code == missing_user.status_code == 401
    # 每个请求的 meta.requestId 和 timestamp 本来就不同，只比较不会泄露账号状态的错误字段。
    assert wrong_password.json()["code"] == missing_user.json()["code"] == 40101
    assert wrong_password.json()["message"] == missing_user.json()["message"]
    assert wrong_password.json()["errors"] == missing_user.json()["errors"] is None
    assert wrong_password.headers["www-authenticate"] == "Bearer"
    assert "refresh_token" in success.cookies


async def test_login_accepts_case_insensitive_email(client: AsyncClient) -> None:
    """OAuth2 username 字段也可以提交邮箱，并且邮箱匹配不区分大小写。"""

    await register(client)
    response = await login(client, username=" ALICE@EXAMPLE.COM ")

    assert response.status_code == 200
    assert response.json()["data"]["access_token"]


async def test_oauth2_token_keeps_standard_response_for_swagger(client: AsyncClient) -> None:
    """Swagger 专用端点保持 OAuth2 标准要求的顶层 access_token。"""

    await register(client)
    response = await client.post(
        "/api/auth/oauth2-token",
        data={"username": "alice", "password": "password123"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert "data" not in response.json()


async def test_refresh_rotates_cookie_and_logout_clears_it(client: AsyncClient) -> None:
    """Refresh 接口轮换 HttpOnly Cookie，退出接口清除浏览器 Cookie。"""

    await register(client)
    login_response = await login(client)
    first_cookie = login_response.cookies.get("refresh_token")
    refreshed = await client.post("/api/auth/refresh")

    assert first_cookie
    assert refreshed.status_code == 200
    assert refreshed.json()["data"]["access_token"]
    assert refreshed.cookies.get("refresh_token") != first_cookie

    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert logout.json()["data"] is None


async def test_create_post_requires_valid_admin_token(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_categories: dict[str, int],
) -> None:
    user = await register(client)
    token = (await login(client)).json()["data"]["access_token"]
    payload = {
        "title": "JWT protected",
        "content": "Only administrators publish.",
        "category_id": seeded_categories["fastapi"],
    }

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
    assert admin.json()["data"]["user_id"] == user["id"]


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


async def test_admin_can_upload_valid_post_image(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path,
    monkeypatch,
) -> None:
    """图片接口校验管理员身份和文件签名，并返回公开 URL。"""

    from app.services import images as image_service

    user = await register(client)
    async with session_factory() as session:
        stored_user = await session.get(User, user["id"])
        assert stored_user is not None
        stored_user.is_admin = True
        await session.commit()
    token = (await login(client)).json()["data"]["access_token"]
    target_dir = tmp_path / "post_images"
    monkeypatch.setattr(image_service, "POST_IMAGE_DIR", target_dir)

    response = await client.post(
        "/api/posts/images",
        files={"image": ("example.png", b"\x89PNG\r\n\x1a\nimage-data", "image/png")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    assert response.json()["data"]["url"].startswith("/media/post_images/")
    assert len(list(target_dir.glob("*.png"))) == 1


async def test_post_image_rejects_regular_user_and_fake_image(client: AsyncClient) -> None:
    """普通用户不能上传，管理员上传伪造图片时返回 400。"""

    user = await register(client)
    token = (await login(client)).json()["data"]["access_token"]
    regular = await client.post(
        "/api/posts/images",
        files={"image": ("fake.png", b"not-an-image", "image/png")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert user["is_admin"] is False
    assert regular.status_code == 403
