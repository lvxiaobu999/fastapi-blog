"""用户异步 CRUD 接口测试。"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
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
    response = await create_user(client, username="Alice", email="Alice@Example.com")

    assert response.status_code == 201
    envelope = response.json()
    body = envelope["data"]
    assert envelope["success"] is True
    assert envelope["code"] == 0
    assert envelope["message"] == "ok"
    assert envelope["meta"]["requestId"] == response.headers["X-Request-ID"]
    assert envelope["meta"]["timestamp"].endswith("+00:00")
    assert body["username"] == "alice"
    assert body["email"] == "alice@example.com"
    assert "hashed_password" not in body

    detail = await client.get(f"/api/users/{body['id']}")
    listing = await client.get("/api/users", params={"offset": 0, "limit": 10})
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == body["id"]
    assert "email" not in detail.json()["data"]
    assert "email" not in listing.json()["data"][0]
    assert [user["id"] for user in listing.json()["data"]] == [body["id"]]

    async with session_factory() as session:
        stored_user = await session.scalar(select(User).where(User.id == body["id"]))
        assert stored_user is not None
        assert await verify_password("password123", stored_user.hashed_password)


async def test_create_rejects_duplicate_identity(client: AsyncClient) -> None:
    assert (await create_user(client)).status_code == 201
    assert (
        await create_user(client, username="ALICE", email="other@example.com")
    ).status_code == 409
    assert (
        await create_user(client, username="other", email="ALICE@example.com")
    ).status_code == 409


async def test_user_can_only_update_nickname_and_email(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = (await create_user(client)).json()["data"]["id"]

    response = await client.patch(
        f"/api/users/{user_id}",
        json={
            "nickname": "Alice",
            "email": "NEW@example.com",
        },
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    assert response.json()["data"]["username"] == "alice"
    assert response.json()["data"]["nickname"] == "Alice"
    assert response.json()["data"]["email"] == "new@example.com"
    async with session_factory() as session:
        stored_user = await session.get(User, user_id)
        assert stored_user is not None
        assert await verify_password("password123", stored_user.hashed_password)


@pytest.mark.parametrize(
    "field,value",
    [("username", "renamed"), ("password", "newpassword123"), ("image_file", "avatar.png")],
)
async def test_profile_update_rejects_fields_with_dedicated_management(
    client: AsyncClient, field: str, value: str
) -> None:
    """普通资料接口不能绕过后台、改密或头像上传流程修改专用字段。"""

    user_id = (await create_user(client)).json()["data"]["id"]

    response = await client.patch(
        f"/api/users/{user_id}", json={field: value}, headers=auth_headers(user_id)
    )

    assert response.status_code == 422


async def test_delete_missing_and_validation_paths(client: AsyncClient) -> None:
    user_id = (await create_user(client)).json()["data"]["id"]

    headers = auth_headers(user_id)
    assert (await client.delete("/api/users/999", headers=headers)).status_code == 404
    deleted = await client.delete(f"/api/users/{user_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["data"] is None
    assert (await client.get(f"/api/users/{user_id}")).status_code == 404
    invalid = await client.post(
        "/api/users",
        json={"username": "x", "email": "invalid", "password": "short"},
    )
    assert invalid.status_code == 422


async def test_user_can_upload_valid_avatar(
    client: AsyncClient,
    tmp_path,
    monkeypatch,
) -> None:
    """头像接口保存随机文件名，并拒绝伪造图片内容。"""

    from app.services import images as image_service

    user_id = (await create_user(client)).json()["data"]["id"]
    headers = auth_headers(user_id)
    monkeypatch.setattr(image_service, "PROFILE_IMAGE_DIR", tmp_path / "profile_pics")

    invalid = await client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"avatar": ("fake.png", b"not-an-image", "image/png")},
    )
    valid = await client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"avatar": ("avatar.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
    )

    assert invalid.status_code == 400
    assert valid.status_code == 200
    assert valid.json()["data"]["image_path"].startswith("/media/profile_pics/")
    assert len(list((tmp_path / "profile_pics").glob("*.png"))) == 1


async def test_replacing_avatar_deletes_previous_file(
    client: AsyncClient,
    tmp_path,
    monkeypatch,
) -> None:
    """数据库成功绑定新头像后，旧文件应被清理且目录只保留当前头像。"""

    from app.services import images as image_service

    user_id = (await create_user(client)).json()["data"]["id"]
    headers = auth_headers(user_id)
    target_dir = tmp_path / "profile_pics"
    monkeypatch.setattr(image_service, "PROFILE_IMAGE_DIR", target_dir)

    first = await client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"avatar": ("first.png", b"\x89PNG\r\n\x1a\nfirst", "image/png")},
    )
    second = await client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"avatar": ("second.png", b"\x89PNG\r\n\x1a\nsecond", "image/png")},
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["image_file"] != second.json()["data"]["image_file"]
    assert [path.name for path in target_dir.glob("*.png")] == [second.json()["data"]["image_file"]]


async def test_avatar_database_failure_removes_new_file(
    client: AsyncClient,
    tmp_path,
    monkeypatch,
) -> None:
    """头像已写盘但数据库提交失败时，应补偿删除没有引用的新文件。"""

    from app.routers import api_users as api_users_router
    from app.services import images as image_service

    user_id = (await create_user(client)).json()["data"]["id"]
    headers = auth_headers(user_id)
    target_dir = tmp_path / "profile_pics"
    monkeypatch.setattr(image_service, "PROFILE_IMAGE_DIR", target_dir)

    async def fail_to_store_avatar(*_args, **_kwargs):
        raise SQLAlchemyError("simulated commit failure")

    monkeypatch.setattr(api_users_router.user_service, "set_profile_image", fail_to_store_avatar)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as failure_client:
        response = await failure_client.post(
            "/api/users/me/avatar",
            headers=headers,
            files={"avatar": ("avatar.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
        )

    assert response.status_code == 500
    assert list(target_dir.glob("*")) == []


async def test_deleting_user_removes_avatar_file(
    client: AsyncClient,
    tmp_path,
    monkeypatch,
) -> None:
    """账号删除提交成功后，应同时清理该用户最后使用的头像文件。"""

    from app.services import images as image_service

    user_id = (await create_user(client)).json()["data"]["id"]
    headers = auth_headers(user_id)
    target_dir = tmp_path / "profile_pics"
    monkeypatch.setattr(image_service, "PROFILE_IMAGE_DIR", target_dir)

    uploaded = await client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"avatar": ("avatar.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
    )
    deleted = await client.delete(f"/api/users/{user_id}", headers=headers)

    assert uploaded.status_code == 200
    assert deleted.status_code == 200
    assert list(target_dir.glob("*")) == []


async def test_admin_user_crud_is_protected(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """普通用户被拒绝，管理员可以新增、修改角色和删除其他用户。"""

    regular_id = (await create_user(client)).json()["data"]["id"]
    regular_headers = auth_headers(regular_id)
    assert (await client.get("/api/admin/users", headers=regular_headers)).status_code == 403

    async with session_factory() as session:
        admin = await session.get(User, regular_id)
        assert admin is not None
        admin.is_admin = True
        await session.commit()

    created = await client.post(
        "/api/admin/users",
        headers=regular_headers,
        json={
            "username": "managed",
            "email": "managed@example.com",
            "password": "password123",
            "nickname": "后台用户",
            "is_admin": False,
        },
    )
    managed_id = created.json()["data"]["id"]
    updated = await client.patch(
        f"/api/admin/users/{managed_id}",
        headers=regular_headers,
        json={"nickname": "内容管理员", "is_admin": True},
    )
    self_delete = await client.delete(f"/api/admin/users/{regular_id}", headers=regular_headers)
    deleted = await client.delete(f"/api/admin/users/{managed_id}", headers=regular_headers)

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["data"]["is_admin"] is True
    assert self_delete.status_code == 400
    assert deleted.status_code == 200
