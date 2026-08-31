"""管理员分类管理接口测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.schemas.post import PostCreate
from app.services.auth import create_access_token
from app.services.posts import create_post

pytestmark = pytest.mark.anyio


def auth_headers(user_id: int) -> dict[str, str]:
    """生成管理员 API 使用的 Bearer Header。"""

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def create_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """为每个用例创建独立管理员，避免依赖历史测试数据。"""

    async with session_factory() as session:
        admin = User(
            username="category_admin",
            email="category_admin@example.com",
            hashed_password="test-hash",
            is_admin=True,
        )
        session.add(admin)
        await session.commit()
        return admin.id


async def test_category_management_requires_admin(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """分类管理接口区分匿名、普通用户和管理员权限。"""

    assert (await client.get("/api/admin/categories")).status_code == 401

    async with session_factory() as session:
        regular = User(
            username="category_regular",
            email="category_regular@example.com",
            hashed_password="test-hash",
        )
        session.add(regular)
        await session.commit()
        regular_id = regular.id

    response = await client.get("/api/admin/categories", headers=auth_headers(regular_id))
    assert response.status_code == 403


async def test_admin_can_create_update_and_delete_category(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_categories: dict[str, int],
) -> None:
    """管理员可以维护未被文章引用的分类，且重复身份被拒绝。"""

    admin_id = await create_admin(session_factory)
    headers = auth_headers(admin_id)

    listing = await client.get("/api/admin/categories", headers=headers)
    assert listing.status_code == 200
    assert [item["slug"] for item in listing.json()["data"]] == ["fastapi", "python", "other"]

    created = await client.post(
        "/api/admin/categories",
        headers=headers,
        json={"name": "  数据库  ", "slug": "database", "sort_order": 5},
    )
    assert created.status_code == 201
    category = created.json()["data"]
    assert category["name"] == "数据库"
    assert category["slug"] == "database"
    category_id = category["id"]

    duplicate = await client.post(
        "/api/admin/categories",
        headers=headers,
        json={"name": "数据库", "slug": "database-2", "sort_order": 6},
    )
    assert duplicate.status_code == 409

    invalid_slug = await client.post(
        "/api/admin/categories",
        headers=headers,
        json={"name": "无效", "slug": "Not A Slug", "sort_order": 0},
    )
    assert invalid_slug.status_code == 422

    updated = await client.patch(
        f"/api/admin/categories/{category_id}",
        headers=headers,
        json={"name": "数据库实践", "slug": "database", "sort_order": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "数据库实践"
    assert updated.json()["data"]["sort_order"] == 1

    deleted = await client.delete(f"/api/admin/categories/{category_id}", headers=headers)
    assert deleted.status_code == 200
    assert all(
        item["id"] != category_id
        for item in (await client.get("/api/admin/categories", headers=headers)).json()["data"]
    )

    missing = await client.delete("/api/admin/categories/99999", headers=headers)
    assert missing.status_code == 404
    assert seeded_categories["other"] > 0


async def test_category_in_use_cannot_be_deleted(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_categories: dict[str, int],
) -> None:
    """被文章引用的分类返回 409，文章分类关系保持不变。"""

    admin_id = await create_admin(session_factory)
    async with session_factory() as session:
        post = await create_post(
            session,
            PostCreate(
                title="Category relation",
                content="Body",
                user_id=admin_id,
                category_ids=[seeded_categories["fastapi"]],
            ),
        )
        post_id = post.id

    response = await client.delete(
        f"/api/admin/categories/{seeded_categories['fastapi']}",
        headers=auth_headers(admin_id),
    )
    assert response.status_code == 409
    assert response.json()["message"] == "Category is used by posts and cannot be deleted"

    async with session_factory() as session:
        stored_post = await session.get(type(post), post_id)
        assert stored_post is not None
