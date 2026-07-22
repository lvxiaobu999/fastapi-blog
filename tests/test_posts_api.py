"""帖子列表与关键词搜索接口测试。"""

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
    """创建供 TestClient 不同线程共享的内存 SQLite Session 工厂。"""

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
    """覆盖真实数据库依赖，确保接口测试不会操作开发数据。"""

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
def seeded_posts(session_factory) -> list[int]:
    """准备可以验证标题搜索、正文搜索和分页顺序的帖子。"""

    with session_factory() as session:
        author = User(username="author", email="author@example.com", hashed_password="hash")
        session.add(author)
        session.commit()

        posts = [
            create_post(
                session,
                PostCreate(title="Learning FastAPI", content="Web framework", user_id=author.id),
            ),
            create_post(
                session,
                PostCreate(title="SQLAlchemy", content="Database mapping", user_id=author.id),
            ),
            create_post(
                session,
                PostCreate(title="Python basics", content="Language notes", user_id=author.id),
            ),
        ]
        return [post.id for post in posts]


def test_list_posts_with_keyword_and_author(
    client: TestClient,
    seeded_posts: list[int],
) -> None:
    response = client.get("/api/posts", params={"keyword": "fastapi"})

    assert response.status_code == 200
    body = response.json()
    assert [post["id"] for post in body] == [seeded_posts[0]]
    assert body[0]["title"] == "Learning FastAPI"
    assert body[0]["author"]["username"] == "author"


def test_list_posts_uses_offset_and_limit(
    client: TestClient,
    seeded_posts: list[int],
) -> None:
    response = client.get("/api/posts", params={"offset": 1, "limit": 1})

    assert response.status_code == 200
    # Service 按创建时间和 ID 倒序，跳过最新一篇后得到第二篇。
    assert [post["id"] for post in response.json()] == [seeded_posts[1]]


@pytest.mark.parametrize(
    "params",
    [
        {"offset": -1},
        {"limit": 0},
        {"limit": 101},
    ],
)
def test_list_posts_rejects_invalid_pagination(client: TestClient, params: dict) -> None:
    response = client.get("/api/posts", params=params)

    assert response.status_code == 422
