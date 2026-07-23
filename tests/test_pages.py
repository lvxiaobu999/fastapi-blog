"""异步 HTML 页面 Router 测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.schemas.post import PostCreate
from app.services.posts import create_post

pytestmark = pytest.mark.anyio


@pytest.fixture
async def seeded_ids(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    async with session_factory() as session:
        user = User(username="author", email="author@example.com", hashed_password="hash")
        session.add(user)
        await session.commit()
        post = await create_post(
            session,
            PostCreate(title="FastAPI page", content="Page body", user_id=user.id),
        )
        return user.id, post.id


async def test_page_router_renders_pages(
    client: AsyncClient,
    seeded_ids: tuple[int, int],
) -> None:
    user_id, post_id = seeded_ids
    routes = [
        "/",
        "/posts",
        "/login",
        "/register",
        "/posts/new",
        f"/posts/{post_id}",
        f"/posts/{post_id}/edit",
        f"/profile/{user_id}",
    ]

    responses = [await client.get(route) for route in routes]

    assert all(response.status_code == 200 for response in responses)
    assert "FastAPI page" in responses[1].text
    assert "author@example.com" in responses[7].text


async def test_page_router_returns_html_404(client: AsyncClient) -> None:
    response = await client.get("/posts/999")

    assert response.status_code == 404
    assert "Page not found" in response.text


async def test_layout_uses_auth_modals_and_es_modules(client: AsyncClient) -> None:
    """导航登录注册只打开模态框，前端写操作由 ES module 接管。"""

    response = await client.get("/")
    assert response.status_code == 200
    assert 'data-bs-target="#loginModal"' in response.text
    assert 'data-bs-target="#registerModal"' in response.text
    assert 'type="module"' in response.text
    assert "/static/js/auth.js" in response.text
