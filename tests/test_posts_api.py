"""帖子异步列表接口测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.schemas.post import PostCreate
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
