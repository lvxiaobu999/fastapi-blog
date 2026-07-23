"""HTML 页面 Router 测试。"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import User
from app.schemas.post import PostCreate
from app.services.posts import create_post


@pytest.fixture
def session_factory():
    """创建供页面测试使用的隔离内存数据库。"""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(session_factory) -> Iterator[TestClient]:
    """覆盖数据库依赖，避免页面测试修改开发数据库。"""

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def seeded_ids(session_factory) -> tuple[int, int]:
    """创建一个用户和一篇帖子，供详情、编辑和资料页面使用。"""

    with session_factory() as session:
        user = User(
            username="author",
            email="author@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.commit()
        post = create_post(
            session,
            PostCreate(title="FastAPI page", content="Page body", user_id=user.id),
        )
        return user.id, post.id


def test_page_router_renders_static_and_post_pages(
    client: TestClient,
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

    responses = [client.get(route) for route in routes]

    assert all(response.status_code == 200 for response in responses)
    assert "FastAPI page" in responses[1].text
    assert "Page body" in responses[5].text
    assert "author@example.com" in responses[7].text


def test_page_router_returns_html_404_for_missing_post(client: TestClient) -> None:
    response = client.get("/posts/999")

    assert response.status_code == 404
    assert "Page not found" in response.text
