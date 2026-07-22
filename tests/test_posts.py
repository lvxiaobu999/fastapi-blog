"""帖子创建与部分更新 Service 测试。"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import User
from app.schemas.post import PostCreate, PostQueryParams, PostUpdate
from app.services.posts import (
    PostAuthorNotFoundError,
    create_post,
    get_post,
    list_posts,
    update_post,
)


@pytest.fixture
def session() -> Iterator[Session]:
    """创建独立内存数据库，不接触开发环境 SQLite 文件。"""

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


@pytest.fixture
def author(session: Session) -> User:
    """准备一个可以关联帖子的作者。"""

    user = User(username="author", email="author@example.com", hashed_password="hash")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_create_post_with_author(session: Session, author: User) -> None:
    post = create_post(
        session,
        PostCreate(title="Original", content="Post body", user_id=author.id),
    )

    assert post.id is not None
    assert post.title == "Original"
    assert post.content == "Post body"
    assert post.user_id == author.id
    assert post.author is author


def test_create_post_rejects_missing_author(session: Session) -> None:
    with pytest.raises(PostAuthorNotFoundError):
        create_post(
            session,
            PostCreate(title="Missing author", content="Post body", user_id=999),
        )


def test_update_post_only_changes_provided_fields(session: Session, author: User) -> None:
    post = create_post(
        session,
        PostCreate(title="Original", content="Original body", user_id=author.id),
    )

    updated_post = update_post(session, post, PostUpdate(title="Changed"))

    assert updated_post.title == "Changed"
    assert updated_post.content == "Original body"
    assert updated_post.user_id == author.id


def test_empty_update_keeps_post_unchanged(session: Session, author: User) -> None:
    post = create_post(
        session,
        PostCreate(title="Original", content="Original body", user_id=author.id),
    )

    unchanged_post = update_post(session, post, PostUpdate())

    assert unchanged_post is post
    assert unchanged_post.title == "Original"
    assert unchanged_post.content == "Original body"


def test_get_post_with_author_or_none(session: Session, author: User) -> None:
    created_post = create_post(
        session,
        PostCreate(title="FastAPI", content="Post body", user_id=author.id),
    )

    found_post = get_post(session, created_post.id)

    assert found_post is not None
    assert found_post.id == created_post.id
    assert found_post.author.id == author.id
    assert get_post(session, 999) is None


def test_list_posts_searches_title_and_content_case_insensitively(
    session: Session,
    author: User,
) -> None:
    fastapi_post = create_post(
        session,
        PostCreate(title="Learning FastAPI", content="Web framework", user_id=author.id),
    )
    database_post = create_post(
        session,
        PostCreate(title="SQLAlchemy", content="DATABASE mapping", user_id=author.id),
    )
    create_post(
        session,
        PostCreate(title="Python basics", content="Language notes", user_id=author.id),
    )

    title_results = list_posts(session, PostQueryParams(keyword="fastapi"))
    content_results = list_posts(session, PostQueryParams(keyword="database"))

    assert [post.id for post in title_results] == [fastapi_post.id]
    assert [post.id for post in content_results] == [database_post.id]


def test_list_posts_supports_stable_pagination(session: Session, author: User) -> None:
    posts = [
        create_post(
            session,
            PostCreate(title=f"Post {number}", content="Body", user_id=author.id),
        )
        for number in range(3)
    ]

    results = list_posts(session, PostQueryParams(offset=1, limit=1))

    # 列表按 created_at 和 id 倒序，最新帖子优先。
    assert [post.id for post in results] == [posts[1].id]


def test_search_treats_like_wildcards_as_plain_text(session: Session, author: User) -> None:
    percent_post = create_post(
        session,
        PostCreate(title="100% FastAPI", content="Body", user_id=author.id),
    )
    create_post(
        session,
        PostCreate(title="Ordinary title", content="Body", user_id=author.id),
    )

    results = list_posts(session, PostQueryParams(keyword="%"))

    assert [post.id for post in results] == [percent_post.id]
