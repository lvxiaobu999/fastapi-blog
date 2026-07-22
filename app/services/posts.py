"""帖子查询与写入业务逻辑。

当前模块实现单篇查询、列表搜索、创建和部分更新，不包含删除、认证或 HTTP 异常处理。
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.post import Post
from app.models.user import User
from app.schemas.post import PostCreate, PostQueryParams, PostUpdate


class PostAuthorNotFoundError(Exception):
    """创建帖子时指定的作者用户不存在。"""


def create_post(session: Session, data: PostCreate) -> Post:
    """验证作者、创建帖子并提交事务。"""

    author = session.get(User, data.user_id)
    if author is None:
        raise PostAuthorNotFoundError

    post = Post(
        title=data.title,
        content=data.content,
        # 通过关系属性赋值后，SQLAlchemy 会在 flush 时同步填写 post.user_id。
        author=author,
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    return post


def update_post(session: Session, post: Post, data: PostUpdate) -> Post:
    """只更新请求中实际提供的标题或正文，并返回更新后的帖子。"""

    changes = data.model_dump(exclude_unset=True)

    # 空 PATCH 没有数据库变更，无需发出 COMMIT 和后续 SELECT。
    if not changes:
        return post

    # PostUpdate 已限制可更新字段，因此可以安全地逐项写回 ORM 对象。
    for field, value in changes.items():
        setattr(post, field, value)

    session.commit()
    session.refresh(post)
    return post


def get_post(session: Session, post_id: int) -> Post | None:
    """按主键查询一篇帖子，并预加载作者；不存在时返回 None。"""

    statement = select(Post).options(selectinload(Post.author)).where(Post.id == post_id)
    return session.scalar(statement)


def _escape_like_keyword(keyword: str) -> str:
    """转义 LIKE 通配符，让用户输入的百分号和下划线按普通字符搜索。"""

    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_posts(session: Session, params: PostQueryParams) -> list[Post]:
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
    return list(session.scalars(statement))


def delete_post(session: Session, post: Post) -> None:
    session.delete(post)
    session.commit()
