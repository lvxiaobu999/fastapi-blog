"""文章点赞、收藏、浏览足迹与个人活动 API 测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Comment, User
from app.schemas.post import PostCreate
from app.services.auth import create_access_token
from app.services.posts import create_post

pytestmark = pytest.mark.anyio


@pytest.fixture
async def activity_data(
    session_factory: async_sessionmaker[AsyncSession], seeded_categories: dict[str, int]
) -> tuple[int, int, dict[str, str]]:
    """创建一名普通用户、一篇文章和一条评论。"""

    async with session_factory() as session:
        user = User(username="reader", email="reader@example.com", hashed_password="hash")
        session.add(user)
        await session.commit()
        post = await create_post(
            session,
            PostCreate(
                title="Activity post",
                content="Body",
                user_id=user.id,
                category_id=seeded_categories["fastapi"],
            ),
        )
        session.add(Comment(content="My comment", post_id=post.id, user_id=user.id))
        await session.commit()
        return user.id, post.id, {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def test_view_count_supports_guests_and_records_authenticated_footprint(
    client: AsyncClient, activity_data: tuple[int, int, dict[str, str]]
) -> None:
    _, post_id, headers = activity_data

    guest = await client.post(f"/api/posts/{post_id}/view")
    member = await client.post(f"/api/posts/{post_id}/view", headers=headers)
    footprints = await client.get(
        "/api/me/activities/posts", params={"kind": "views"}, headers=headers
    )

    assert guest.status_code == member.status_code == footprints.status_code == 200
    assert guest.json()["data"]["view_count"] == 1
    assert member.json()["data"]["view_count"] == 2
    assert [item["id"] for item in footprints.json()["data"]] == [post_id]


async def test_like_and_favorite_toggle_without_duplicate_relations(
    client: AsyncClient, activity_data: tuple[int, int, dict[str, str]]
) -> None:
    _, post_id, headers = activity_data

    unauthorized = await client.post(f"/api/posts/{post_id}/like")
    liked = await client.post(f"/api/posts/{post_id}/like", headers=headers)
    favorited = await client.post(f"/api/posts/{post_id}/favorite", headers=headers)
    likes = await client.get("/api/me/activities/posts", params={"kind": "likes"}, headers=headers)
    favorites = await client.get(
        "/api/me/activities/posts", params={"kind": "favorites"}, headers=headers
    )
    unliked = await client.post(f"/api/posts/{post_id}/like", headers=headers)

    assert unauthorized.status_code == 401
    assert liked.json()["data"]["liked"] is True
    assert liked.json()["data"]["like_count"] == 1
    assert favorited.json()["data"]["favorited"] is True
    assert [item["id"] for item in likes.json()["data"]] == [post_id]
    assert [item["id"] for item in favorites.json()["data"]] == [post_id]
    assert unliked.json()["data"]["liked"] is False
    assert unliked.json()["data"]["like_count"] == 0


async def test_comment_activity_returns_post_context(
    client: AsyncClient, activity_data: tuple[int, int, dict[str, str]]
) -> None:
    _, post_id, headers = activity_data

    response = await client.get("/api/me/activities/comments", headers=headers)

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "id": 1,
            "content": "My comment",
            "created_at": response.json()["data"][0]["created_at"],
            "post_id": post_id,
            "post_title": "Activity post",
        }
    ]


async def test_activity_endpoints_return_404_for_missing_post(
    client: AsyncClient, activity_data: tuple[int, int, dict[str, str]]
) -> None:
    _, _, headers = activity_data

    assert (await client.post("/api/posts/999/view")).status_code == 404
    assert (await client.post("/api/posts/999/favorite", headers=headers)).status_code == 404
