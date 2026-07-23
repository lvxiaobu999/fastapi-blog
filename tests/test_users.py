"""用户异步 CRUD 接口测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.services.auth import create_access_token, verify_password

pytestmark = pytest.mark.anyio


async def create_user(
    client: AsyncClient,
    *,
    username: str = "alice",
    email: str = "alice@example.com",
):
    return await client.post(
        "/api/users",
        json={"username": username, "email": email, "password": "password123"},
    )


def auth_headers(user_id: int) -> dict[str, str]:
    """生成用户本人调用受保护资料接口所需的 Header。"""

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def test_create_read_and_list_user(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await create_user(client, email="Alice@Example.com")

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert "hashed_password" not in body

    detail = await client.get(f"/api/users/{body['id']}")
    listing = await client.get("/api/users", params={"offset": 0, "limit": 10})
    assert detail.status_code == 200
    assert [user["id"] for user in listing.json()] == [body["id"]]

    async with session_factory() as session:
        stored_user = await session.scalar(select(User).where(User.id == body["id"]))
        assert stored_user is not None
        assert await verify_password("password123", stored_user.hashed_password)


async def test_create_rejects_duplicate_identity(client: AsyncClient) -> None:
    assert (await create_user(client)).status_code == 201
    assert (await create_user(client, email="other@example.com")).status_code == 409
    assert (
        await create_user(client, username="other", email="ALICE@example.com")
    ).status_code == 409


async def test_update_user_and_password(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = (await create_user(client)).json()["id"]

    response = await client.patch(
        f"/api/users/{user_id}",
        json={"nickname": "Alice", "email": "NEW@example.com", "password": "newpassword123"},
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    assert response.json()["email"] == "new@example.com"
    async with session_factory() as session:
        stored_user = await session.get(User, user_id)
        assert stored_user is not None
        assert await verify_password("newpassword123", stored_user.hashed_password)


async def test_delete_missing_and_validation_paths(client: AsyncClient) -> None:
    user_id = (await create_user(client)).json()["id"]

    headers = auth_headers(user_id)
    assert (await client.delete("/api/users/999", headers=headers)).status_code == 404
    assert (await client.delete(f"/api/users/{user_id}", headers=headers)).status_code == 204
    assert (await client.get(f"/api/users/{user_id}")).status_code == 404
    invalid = await client.post(
        "/api/users",
        json={"username": "x", "email": "invalid", "password": "short"},
    )
    assert invalid.status_code == 422
