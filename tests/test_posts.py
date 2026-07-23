"""帖子异步 Service 测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.schemas.post import PostCreate, PostQueryParams, PostUpdate
from app.services.posts import (
    PostAuthorNotFoundError,
    create_post,
    get_post,
    list_posts,
    update_post,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as database_session:
        yield database_session


@pytest.fixture
async def author(session: AsyncSession) -> User:
    user = User(username="author", email="author@example.com", hashed_password="hash")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_create_post_with_author(session: AsyncSession, author: User) -> None:
    post = await create_post(
        session,
        PostCreate(title="Original", content="Post body", user_id=author.id),
    )

    assert post.id is not None
    assert post.content == "Post body"
    assert post.user_id == author.id


async def test_create_post_rejects_missing_author(session: AsyncSession) -> None:
    with pytest.raises(PostAuthorNotFoundError):
        await create_post(
            session,
            PostCreate(title="Missing author", content="Post body", user_id=999),
        )


async def test_update_post_only_changes_provided_fields(
    session: AsyncSession,
    author: User,
) -> None:
    post = await create_post(
        session,
        PostCreate(title="Original", content="Original body", user_id=author.id),
    )

    updated_post = await update_post(session, post, PostUpdate(title="Changed"))

    assert updated_post.title == "Changed"
    assert updated_post.content == "Original body"


async def test_empty_update_keeps_post_unchanged(
    session: AsyncSession,
    author: User,
) -> None:
    post = await create_post(
        session,
        PostCreate(title="Original", content="Original body", user_id=author.id),
    )

    unchanged_post = await update_post(session, post, PostUpdate())

    assert unchanged_post is post


async def test_get_post_with_author_or_none(session: AsyncSession, author: User) -> None:
    created_post = await create_post(
        session,
        PostCreate(title="FastAPI", content="Post body", user_id=author.id),
    )

    found_post = await get_post(session, created_post.id)

    assert found_post is not None
    assert found_post.author.id == author.id
    assert await get_post(session, 999) is None


async def test_list_posts_searches_title_and_content(
    session: AsyncSession,
    author: User,
) -> None:
    fastapi_post = await create_post(
        session,
        PostCreate(title="Learning FastAPI", content="Web framework", user_id=author.id),
    )
    database_post = await create_post(
        session,
        PostCreate(title="SQLAlchemy", content="DATABASE mapping", user_id=author.id),
    )

    title_results = await list_posts(session, PostQueryParams(keyword="fastapi"))
    content_results = await list_posts(session, PostQueryParams(keyword="database"))

    assert [post.id for post in title_results] == [fastapi_post.id]
    assert [post.id for post in content_results] == [database_post.id]


async def test_list_posts_supports_pagination(session: AsyncSession, author: User) -> None:
    posts = [
        await create_post(
            session,
            PostCreate(title=f"Post {number}", content="Body", user_id=author.id),
        )
        for number in range(3)
    ]

    results = await list_posts(session, PostQueryParams(offset=1, limit=1))

    assert [post.id for post in results] == [posts[1].id]


async def test_search_escapes_like_wildcards(session: AsyncSession, author: User) -> None:
    percent_post = await create_post(
        session,
        PostCreate(title="100% FastAPI", content="Body", user_id=author.id),
    )
    await create_post(
        session,
        PostCreate(title="Ordinary title", content="Body", user_id=author.id),
    )

    results = await list_posts(session, PostQueryParams(keyword="%"))

    assert [post.id for post in results] == [percent_post.id]
