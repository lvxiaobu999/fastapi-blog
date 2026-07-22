from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import User
from app.services.users import password_hash


@pytest.fixture
def session_factory():
    """为每个测试创建共享同一内存连接的 SQLite Session 工厂。"""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        # 内存 SQLite 的数据库随连接存在；StaticPool 让 TestClient 线程复用同一连接。
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
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


def create_user(client: TestClient, *, username: str = "alice", email: str = "alice@example.com"):
    return client.post(
        "/api/users",
        json={"username": username, "email": email, "password": "password123"},
    )


def test_create_read_and_list_user(client: TestClient, session_factory) -> None:
    response = create_user(client, email="Alice@Example.com")

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert body["nickname"].startswith("user_")
    assert body["is_admin"] is False
    assert body["image_path"] == "/static/images/default.jpg"
    assert "password" not in body
    assert "hashed_password" not in body

    detail = client.get(f"/api/users/{body['id']}")
    listing = client.get("/api/users", params={"offset": 0, "limit": 10})
    assert detail.status_code == 200
    assert listing.status_code == 200
    assert [user["id"] for user in listing.json()] == [body["id"]]

    with session_factory() as session:
        stored_user = session.scalar(select(User).where(User.id == body["id"]))
        assert stored_user is not None
        assert stored_user.hashed_password != "password123"
        assert password_hash.verify("password123", stored_user.hashed_password)


def test_create_rejects_duplicate_username_or_email(client: TestClient) -> None:
    assert create_user(client).status_code == 201

    duplicate_username = create_user(client, email="other@example.com")
    duplicate_email = create_user(client, username="other", email="ALICE@example.com")

    assert duplicate_username.status_code == 409
    assert duplicate_email.status_code == 409


def test_update_user_and_password(client: TestClient, session_factory) -> None:
    user_id = create_user(client).json()["id"]

    response = client.patch(
        f"/api/users/{user_id}",
        json={"nickname": "Alice", "email": "NEW@example.com", "password": "newpassword123"},
    )

    assert response.status_code == 200
    assert response.json()["nickname"] == "Alice"
    assert response.json()["email"] == "new@example.com"
    with session_factory() as session:
        stored_user = session.get(User, user_id)
        assert stored_user is not None
        assert password_hash.verify("newpassword123", stored_user.hashed_password)


def test_delete_missing_and_validation_paths(client: TestClient) -> None:
    user_id = create_user(client).json()["id"]

    assert client.delete(f"/api/users/{user_id}").status_code == 204
    assert client.get(f"/api/users/{user_id}").status_code == 404
    assert client.delete("/api/users/999").status_code == 404
    assert client.post(
        "/api/users",
        json={"username": "x", "email": "invalid", "password": "short"},
    ).status_code == 422
