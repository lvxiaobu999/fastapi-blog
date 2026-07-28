"""帖子查询与写入业务逻辑。

当前模块实现单篇查询、列表搜索、创建和部分更新，不包含删除、认证或 HTTP 异常处理。
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.models.category import Category
from app.models.post import Post
from app.models.user import User
from app.schemas.post import PostCreate, PostQueryParams, PostTitleSearchParams, PostUpdate
from app.services import categories as category_service


class PostAuthorNotFoundError(Exception):
    """创建帖子时指定的作者用户不存在。"""


class PostCategoryNotFoundError(Exception):
    """创建或更新帖子时指定的分类不存在。"""


async def create_post(session: AsyncSession, data: PostCreate) -> Post:
    """验证作者与分类、创建帖子并提交事务。"""

    author = await session.get(User, data.user_id)
    if author is None:
        raise PostAuthorNotFoundError

    category = (
        await category_service.get_category_by_id(session, data.category_id)
        if data.category_id is not None
        else await category_service.get_default_category(session)
    )
    if category is None:
        raise PostCategoryNotFoundError

    post = Post(
        title=data.title,
        content=data.content,
        # 通过关系属性赋值后，SQLAlchemy 会在 flush 时同步填写外键；这样返回对象也已经
        # 持有作者和分类，不需要在响应序列化阶段触发异步懒加载。
        author=author,
        category=category,
    )
    session.add(post)
    await session.commit()
    # commit/refresh 可能使关系属性过期；统一走详情查询重新加载公开响应需要的作者和分类，
    # 避免 Pydantic 在异步上下文外触发懒加载并抛出 MissingGreenlet。
    created_post = await get_post(session, post.id)
    if created_post is None:  # pragma: no cover - 提交成功后主键查询不应消失。
        raise RuntimeError("Created post could not be reloaded")
    return created_post


async def update_post(session: AsyncSession, post: Post, data: PostUpdate) -> Post:
    """只更新请求中实际提供的标题或正文，并返回更新后的帖子。"""

    changes = data.model_dump(exclude_unset=True)

    # 空 PATCH 没有数据库变更，无需发出 COMMIT 和后续 SELECT。
    if not changes:
        return post

    if "category_id" in changes:
        category = await category_service.get_category_by_id(
            session, changes.pop("category_id")
        )
        if category is None:
            raise PostCategoryNotFoundError
        post.category = category

    # PostUpdate 已限制可更新字段，因此可以安全地逐项写回 ORM 对象。
    for field, value in changes.items():
        setattr(post, field, value)

    await session.commit()
    updated_post = await get_post(session, post.id)
    if updated_post is None:  # pragma: no cover - 更新期间没有删除路径。
        raise RuntimeError("Updated post could not be reloaded")
    return updated_post


async def get_post(session: AsyncSession, post_id: int) -> Post | None:
    """按主键查询一篇帖子，并预加载作者和分类；不存在时返回 None。"""

    statement = (
        select(Post)
        .options(selectinload(Post.author), selectinload(Post.category))
        .where(Post.id == post_id)
    )
    return await session.scalar(statement)


def _escape_like_keyword(keyword: str) -> str:
    """转义 LIKE 通配符，让用户输入的百分号和下划线按普通字符搜索。"""

    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_posts(session: AsyncSession, params: PostQueryParams) -> list[Post]:
    """分页查询帖子，并按关键词或分类 slug 筛选。"""

    statement = select(Post).options(
        selectinload(Post.author), selectinload(Post.category)
    )

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

    category_slug = params.category.strip() if params.category is not None else None
    if category_slug:
        # 分类名称可能调整，页面 URL 使用稳定 slug；EXISTS 过滤不会改变 Post 查询的列结构。
        statement = statement.where(Post.category.has(Category.slug == category_slug))

    # created_at 相同时再按 id 排序，保证分页结果顺序稳定。
    statement = (
        statement.order_by(Post.created_at.desc(), Post.id.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    result = await session.scalars(statement)
    return list(result)


async def search_post_titles(
    session: AsyncSession, params: PostTitleSearchParams
) -> list[Post]:
    """按标题模糊搜索少量候选项，供全局搜索弹窗实时展示。"""

    keyword = params.keyword.strip()
    if not keyword:
        return []

    pattern = f"%{_escape_like_keyword(keyword)}%"
    statement = (
        select(Post)
        # 搜索下拉只需要跳转主键和标题，避免每次按键都读取正文及关联对象。
        .options(load_only(Post.id, Post.title))
        .where(Post.title.ilike(pattern, escape="\\"))
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(params.limit)
    )
    result = await session.scalars(statement)
    return list(result)


async def delete_post(session: AsyncSession, post: Post) -> None:
    """异步删除帖子并提交事务。"""

    await session.delete(post)
    await session.commit()
