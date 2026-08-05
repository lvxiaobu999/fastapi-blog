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

# ==================== Service 入口导读 ====================
# 上游调用者：pages、api_posts、api_activities 和 comments Router。
# 本模块负责文章详情/列表/标题搜索以及创建、更新、删除，并预加载 author/category。
# 分类存在性由 categories Service 协助验证；Router 负责管理员权限和 HTTP 异常转换。
# create/update/delete 在本模块提交事务，纯查询函数不会 commit。


class PostAuthorNotFoundError(Exception):
    """创建帖子时指定的作者用户不存在。"""


class PostCategoryNotFoundError(Exception):
    """创建或更新帖子时指定的分类不存在。"""


async def create_post(session: AsyncSession, data: PostCreate) -> Post:
    """完成有权限用户在后台发布文章的持久化流程。

    发布表单提交标题、正文和分类，认证用户 ID 由 Router 写入请求数据。这里确认作者和
    分类真实存在，防止生成无法展示的文章；保存后重新加载作者/分类，使创建响应能直接
    更新或跳转页面。权限不在这里判断，同一函数也可供测试和可信内部任务复用。
    """

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
        summary=data.summary,
        cover_image_url=data.cover_image_url,
        content=data.content,
        is_published=data.is_published,
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
    """保存后台文章编辑页实际修改的内容，而不覆盖未编辑字段。

    PATCH 表单可能只改标题、正文或分类；``exclude_unset`` 保留没有提交的旧值。分类必须
    重新确认存在，空修改不产生事务。提交后重新取得关联数据，供管理列表或详情页展示。
    """

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


async def get_post(
    session: AsyncSession, post_id: int, *, include_unpublished: bool = True
) -> Post | None:
    """为详情、编辑、评论和互动入口提供同一份“可用文章”查询。

    这些功能在继续执行前都必须按 URL 中的 ID 找到文章，并需要作者/分类用于响应渲染。
    因此提前预加载关系；找不到返回 None，由不同 Router 分别决定 404 或 WebSocket 错误。
    """

    statement = (
        select(Post)
        .options(selectinload(Post.author), selectinload(Post.category))
        .where(Post.id == post_id)
    )
    if not include_unpublished:
        statement = statement.where(Post.is_published.is_(True))
    return await session.scalar(statement)


def _escape_like_keyword(keyword: str) -> str:
    """保证搜索框输入的 `%`、`_` 表示字符本身，而不是偷偷扩大查询范围。

    文章列表和标题联想都使用 SQL LIKE；集中转义能让两个搜索入口行为一致，也避免用户
    输入通配符后意外匹配全部内容。这是搜索业务的内部安全/正确性辅助步骤。
    """

    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_posts(
    session: AsyncSession,
    params: PostQueryParams,
    *,
    include_unpublished: bool = False,
) -> list[Post]:
    """为搜索结果页、分类结果页和后台文章列表生成帖子集合。

    首页点击搜索时携带 keyword 跳到列表页，点击类型 Tag 时携带 category；后台也复用列表。
    本函数组合这些可选条件、预加载展示字段并分页排序。这样首页无需加载文章，所有结果页
    仍共享一致筛选规则。它只读取数据库，不记录浏览；浏览必须在真正进入详情页时发生。
    """

    statement = select(Post).options(
        selectinload(Post.author), selectinload(Post.category)
    )
    if not include_unpublished:
        # 公开搜索、分类和首页只展示上架文章；后台通过显式可信参数读取全部。
        statement = statement.where(Post.is_published.is_(True))

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
    """为顶部导航搜索模态框提供输入过程中的标题联想候选。

    用户每次停止输入（前端防抖后）会调用此查询，点击候选直接进入详情页。因此只需要文章
    ID 和标题，不需要正文、作者等大字段；限制结果数量也能降低频繁 AJAX 请求的数据库和
    网络成本。正式提交搜索仍进入 ``list_posts`` 支持标题与正文的完整结果。
    """

    keyword = params.keyword.strip()
    if not keyword:
        return []

    pattern = f"%{_escape_like_keyword(keyword)}%"
    statement = (
        select(Post)
        # 搜索下拉只需要跳转主键和标题，避免每次按键都读取正文及关联对象。
        .options(load_only(Post.id, Post.title))
        .where(Post.title.ilike(pattern, escape="\\"))
        .where(Post.is_published.is_(True))
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(params.limit)
    )
    result = await session.scalars(statement)
    return list(result)


async def delete_post(session: AsyncSession, post: Post) -> None:
    """执行后台文章管理表中的删除操作并提交。

    api_posts Router 在调用前负责认证、管理员权限和文章存在性；这里保持单一职责，只执行
    持久化删除。评论及互动记录的清理由数据库级联约束保证，避免遗留指向不存在文章的数据。
    """

    await session.delete(post)
    await session.commit()
