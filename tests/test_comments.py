"""评论持久化、公开接口与 WebSocket 协议测试。"""

from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import get_db
from app.main import app
from app.models import User
from app.schemas.comment import CommentCreate
from app.schemas.post import PostCreate
from app.services import comments as comment_service
from app.services.posts import create_post

pytestmark = pytest.mark.anyio


async def test_websocket_comment_rate_limit_closes_burst_connection() -> None:
    """单连接在时间窗口内超过消息上限时应收到错误并关闭。"""

    from app.routers.comments import (
        COMMENT_RATE_LIMIT_MAX_MESSAGES,
        WebSocketContextInvalidError,
        _enforce_comment_rate_limit,
    )

    class FakeWebSocket:
        def __init__(self) -> None:
            self.messages: list[dict[str, str]] = []
            self.closed = False

        async def send_json(self, message: dict[str, str]) -> None:
            self.messages.append(message)

        async def close(self, *, code: int) -> None:
            self.closed = code == 1008

    websocket = FakeWebSocket()
    message_times: deque[float] = deque()
    for _ in range(COMMENT_RATE_LIMIT_MAX_MESSAGES):
        await _enforce_comment_rate_limit(websocket, message_times)  # type: ignore[arg-type]

    with pytest.raises(WebSocketContextInvalidError):
        await _enforce_comment_rate_limit(websocket, message_times)  # type: ignore[arg-type]

    assert websocket.messages[-1]["type"] == "error"
    assert websocket.closed is True


@pytest.fixture
async def comment_post(
    session_factory: async_sessionmaker[AsyncSession], seeded_categories: dict[str, int]
) -> tuple[int, int]:
    """创建评论测试所需的作者、读者和帖子。"""

    async with session_factory() as session:
        author = User(username="writer", email="writer@example.com", hashed_password="hash")
        reader = User(username="reader", email="reader@example.com", hashed_password="hash")
        session.add_all([author, reader])
        await session.commit()
        post = await create_post(
            session,
            PostCreate(
                title="Commentable post",
                content="Body",
                user_id=author.id,
                category_ids=[seeded_categories["fastapi"]],
            ),
        )
        return post.id, reader.id


async def test_create_and_list_comments(
    session_factory: async_sessionmaker[AsyncSession], comment_post: tuple[int, int]
) -> None:
    """评论提交后可按时间读取，并包含最小作者公开信息。"""

    post_id, reader_id = comment_post
    async with session_factory() as session:
        created = await comment_service.create_comment(
            session, post_id, reader_id, CommentCreate(content="  很有帮助  ")
        )
        reply = await comment_service.create_comment(
            session,
            post_id,
            reader_id,
            CommentCreate(content="第一条回复", parent_id=created.id),
        )
        nested_reply = await comment_service.create_comment(
            session,
            post_id,
            reader_id,
            CommentCreate(content="回复另一条回复", parent_id=reply.id),
        )
        comments = await comment_service.list_comments(session, post_id)

    assert created.content == "很有帮助"
    assert [comment.id for comment in comments] == [created.id, reply.id, nested_reply.id]
    assert comments[0].author.username == "reader"
    assert reply.parent_id == created.id
    assert reply.root_id == created.id
    assert nested_reply.parent_id == reply.id
    assert nested_reply.root_id == created.id
    assert nested_reply.reply_to_author.id == reader_id


async def test_comment_history_api_and_post_page(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    comment_post: tuple[int, int],
) -> None:
    """历史接口使用统一响应，详情页加载实时评论脚本。"""

    post_id, reader_id = comment_post
    async with session_factory() as session:
        await comment_service.create_comment(
            session, post_id, reader_id, CommentCreate(content="API comment")
        )

    response = await client.get(f"/api/posts/{post_id}/comments")
    page = await client.get(f"/posts/{post_id}")

    assert response.status_code == 200
    assert response.json()["data"][0]["content"] == "API comment"
    assert "email" not in response.json()["data"][0]["author"]
    assert "data-comments" in page.text
    assert "/static/js/comments.js" in page.text


def test_comment_websocket_authenticates_and_broadcasts(monkeypatch: pytest.MonkeyPatch) -> None:
    """WebSocket 只在认证后接受创建消息，并返回已持久化的评论。"""

    from app.routers import comments as comments_router

    user = SimpleNamespace(id=7)
    post = SimpleNamespace(id=11, is_published=True)

    class FakeSession:
        async def get(self, model, key, **_kwargs):
            return post if model.__name__ == "Post" else user

    async def override_get_db():
        yield FakeSession()

    async def fake_create_comment(_session, post_id, user_id, data):
        return SimpleNamespace(
            id=5,
            content=data.content,
            created_at=datetime.now(UTC),
            post_id=post_id,
            parent_id=data.parent_id,
            root_id=1 if data.parent_id else None,
            author=SimpleNamespace(id=user_id, nickname="读者", image_path="/avatar.png"),
            reply_to_author=(
                SimpleNamespace(id=1, nickname="作者", image_path="/author.png")
                if data.parent_id
                else None
            ),
        )

    monkeypatch.setattr(comments_router, "verify_access_token", lambda _token: 7)
    monkeypatch.setattr(comments_router.comment_service, "create_comment", fake_create_comment)
    app.dependency_overrides[get_db] = override_get_db
    try:
        with (
            TestClient(app) as test_client,
            test_client.websocket_connect("/api/posts/11/comments/ws") as websocket,
        ):
            websocket.send_json({"type": "authenticate", "token": "valid"})
            assert websocket.receive_json()["type"] == "authenticated"
            websocket.send_json({"type": "comment.create", "content": "实时评论", "parent_id": 1})
            assert websocket.receive_json()["type"] == "comment.submitted"
            message = websocket.receive_json()
            assert message["type"] == "comment.created"
            assert message["data"]["content"] == "实时评论"
            assert message["data"]["parent_id"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
