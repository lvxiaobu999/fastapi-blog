"""帖子异步列表接口测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.schemas.post import PostCreate
from app.services.auth import create_access_token
from app.services.posts import create_post

pytestmark = pytest.mark.anyio


@pytest.fixture
async def seeded_posts(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_categories: dict[str, int],
) -> list[int]:
    async with session_factory() as session:
        author = User(username="author", email="author@example.com", hashed_password="hash")
        session.add(author)
        await session.commit()

        posts = [
            await create_post(
                session,
                PostCreate(
                    title="Learning FastAPI",
                    content="Web framework",
                    user_id=author.id,
                    category_id=seeded_categories["fastapi"],
                ),
            ),
            await create_post(
                session,
                PostCreate(
                    title="SQLAlchemy",
                    content="Database mapping",
                    user_id=author.id,
                    category_id=seeded_categories["python"],
                ),
            ),
            await create_post(
                session,
                PostCreate(
                    title="Python basics",
                    content="Language notes",
                    user_id=author.id,
                    category_id=seeded_categories["python"],
                ),
            ),
        ]
        return [post.id for post in posts]


async def test_list_posts_with_keyword_and_author(
    client: AsyncClient,
    seeded_posts: list[int],
) -> None:
    response = await client.get("/api/posts", params={"keyword": "fastapi"})

    assert response.status_code == 200
    body = response.json()["data"]
    assert [post["id"] for post in body] == [seeded_posts[0]]
    assert body[0]["author"]["username"] == "author"
    assert body[0]["category"]["slug"] == "fastapi"


async def test_list_posts_filters_category(
    client: AsyncClient,
    seeded_posts: list[int],
) -> None:
    response = await client.get("/api/posts", params={"category": "python"})

    assert response.status_code == 200
    assert [post["id"] for post in response.json()["data"]] == list(
        reversed(seeded_posts[1:])
    )


async def test_search_endpoint_returns_title_only(
    client: AsyncClient,
    seeded_posts: list[int],
) -> None:
    response = await client.get("/api/posts/search", params={"keyword": "fastapi"})
    content_only = await client.get("/api/posts/search", params={"keyword": "database"})

    assert response.status_code == 200
    assert response.json()["data"] == [
        {"id": seeded_posts[0], "title": "Learning FastAPI"}
    ]
    assert content_only.json()["data"] == []


async def test_list_posts_uses_offset_and_limit(
    client: AsyncClient,
    seeded_posts: list[int],
) -> None:
    response = await client.get("/api/posts", params={"offset": 1, "limit": 1})

    assert response.status_code == 200
    assert [post["id"] for post in response.json()["data"]] == [seeded_posts[1]]


@pytest.mark.parametrize(
    "params",
    [{"offset": -1}, {"limit": 0}, {"limit": 101}, {"category": "x" * 51}],
)
async def test_list_posts_rejects_invalid_pagination(
    client: AsyncClient,
    params: dict,
) -> None:
    response = await client.get("/api/posts", params=params)

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["code"] == 42201
    assert body["message"] == "Request validation failed"
    assert body["data"] is None
    assert isinstance(body["errors"], list)
    assert {item["field"] for item in body["errors"]} & {
        "offset",
        "limit",
        "category",
    }


async def test_admin_can_list_and_toggle_unpublished_post(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_categories: dict[str, int],
) -> None:
    """公开接口隐藏下架文章，管理员仍可管理并重新上架。"""

    async with session_factory() as session:
        admin = User(
            username="post_admin",
            email="post_admin@example.com",
            hashed_password="hash",
            is_admin=True,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        post = await create_post(
            session,
            PostCreate(
                title="Temporary draft",
                summary="Not public yet",
                cover_image_url="/media/post_images/draft.png",
                content="Draft body",
                category_id=seeded_categories["fastapi"],
                user_id=admin.id,
                is_published=False,
            ),
        )
        admin_id, post_id = admin.id, post.id

    headers = {"Authorization": f"Bearer {create_access_token(admin_id)}"}
    public_list = await client.get("/api/posts", params={"keyword": "Temporary"})
    public_detail = await client.get(f"/api/posts/{post_id}")
    unauthenticated_admin_list = await client.get("/api/posts/admin")
    admin_list = await client.get("/api/posts/admin", headers=headers)
    published = await client.patch(
        f"/api/posts/{post_id}", headers=headers, json={"is_published": True}
    )
    visible_detail = await client.get(f"/api/posts/{post_id}")

    assert public_list.json()["data"] == []
    assert public_detail.status_code == 404
    assert unauthenticated_admin_list.status_code == 401
    assert admin_list.status_code == 200
    assert admin_list.json()["data"][0]["is_published"] is False
    assert admin_list.json()["data"][0]["summary"] == "Not public yet"
    assert published.status_code == 200
    assert published.json()["data"]["is_published"] is True
    assert visible_detail.status_code == 200


async def test_post_rejects_external_cover_url(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_categories: dict[str, int],
) -> None:
    """横图必须先经过站内上传接口，不能直接保存任意外部 URL。"""

    async with session_factory() as session:
        admin = User(
            username="cover_admin",
            email="cover_admin@example.com",
            hashed_password="hash",
            is_admin=True,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        admin_id = admin.id

    response = await client.post(
        "/api/posts",
        headers={"Authorization": f"Bearer {create_access_token(admin_id)}"},
        json={
            "title": "Unsafe cover",
            "content": "Body",
            "category_id": seeded_categories["fastapi"],
            "cover_image_url": "https://tracker.example/cover.png",
        },
    )

    assert response.status_code == 422
