"""帖子查询与写入业务逻辑。

当前模块实现单篇查询、列表搜索、创建和部分更新，不包含删除、认证或 HTTP 异常处理。
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.post import Post
from app.models.user import User
from app.schemas.post import PostCreate, PostQueryParams, PostUpdate


class PostAuthorNotFoundError(Exception):
    """创建帖子时指定的作者用户不存在。"""


async def create_post(session: AsyncSession, data: PostCreate) -> Post:
    """验证作者、创建帖子并提交事务。"""

    author = await session.get(User, data.user_id)
    if author is None:
        raise PostAuthorNotFoundError

    post = Post(
        title=data.title,
        content=data.content,
        # 通过关系属性赋值后，SQLAlchemy 会在 flush 时同步填写 post.user_id。
        author=author,
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)

    return post


async def update_post(session: AsyncSession, post: Post, data: PostUpdate) -> Post:
    """只更新请求中实际提供的标题或正文，并返回更新后的帖子。"""

    changes = data.model_dump(exclude_unset=True)

    # 空 PATCH 没有数据库变更，无需发出 COMMIT 和后续 SELECT。
    if not changes:
        return post

    # PostUpdate 已限制可更新字段，因此可以安全地逐项写回 ORM 对象。
    for field, value in changes.items():
        setattr(post, field, value)

    await session.commit()
    await session.refresh(post)
    return post


async def get_post(session: AsyncSession, post_id: int) -> Post | None:
    """按主键查询一篇帖子，并预加载作者；不存在时返回 None。"""

    statement = select(Post).options(selectinload(Post.author)).where(Post.id == post_id)
    return await session.scalar(statement)


def _escape_like_keyword(keyword: str) -> str:
    """转义 LIKE 通配符，让用户输入的百分号和下划线按普通字符搜索。"""

    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_posts(session: AsyncSession, params: PostQueryParams) -> list[Post]:
    """分页查询帖子，并按关键词模糊匹配标题或正文。"""

    statement = select(Post).options(selectinload(Post.author))

    # 去除首尾空格后为空，等同于没有关键词；避免 "%%" 这类无意义过滤条件。
    keyword = params.keyword.strip() if params.keyword is not None else None
    if keyword:
        pattern = f"%{_escape_like_keyword(keyword)}%"
        statement = statement.where(
            or_(
                Post.title.ilike(pattern, escape="\\"),
                Post.content.ilike(pattern, escape="\\"),
            )
        )

    # created_at 相同时再按 id 排序，保证分页结果顺序稳定。
    statement = (
        statement.order_by(Post.created_at.desc(), Post.id.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    result = await session.scalars(statement)
    return list(result)


async def delete_post(session: AsyncSession, post: Post) -> None:
    """异步删除帖子并提交事务。"""

    await session.delete(post)
    await session.commit()
