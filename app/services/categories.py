"""博客分类查询与管理员维护服务。

本模块提供分类读取、帖子写入时的分类校验和管理员 CRUD，不负责 HTTP 参数、模板渲染或权限
判断。Router 负责管理员身份与状态码，Service 负责规范化、唯一性、关联检查和事务边界。
"""

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.post import post_categories
from app.schemas.category import CategoryCreate, CategoryUpdate

# ==================== Service 入口导读 ====================
# 上游调用者：pages Router、posts Service 和 api_categories Router。
# 本模块不处理 HTTP、权限或模板；list/ID/slug 查询供首页、编辑器和文章校验使用，管理员
# CRUD 在这里集中处理规范化、唯一性、文章关联保护和事务提交。查询找不到时返回 None，
# 由上游根据页面或 API 协议转换为对应响应。

DEFAULT_CATEGORY_SLUG = "other"


class CategoryAlreadyExistsError(Exception):
    """分类名称或 slug 已被其他分类占用。"""


class CategoryInUseError(Exception):
    """分类仍被文章引用，不能删除。"""


def _normalize_name(name: str) -> str:
    """统一分类名称首尾空白，名称唯一性由数据库和 Service 共同保证。"""

    return name.strip()


def _normalize_slug(slug: str) -> str:
    """统一 slug 大小写和首尾空白，保持公开分类 URL 稳定。"""

    return slug.strip().lower()


async def _ensure_unique(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    exclude_category_id: int | None = None,
) -> None:
    """在写入前给出清晰的重复提示；数据库唯一约束兜底并发请求。"""

    statement = select(Category.id).where(
        (func.lower(Category.name) == name.lower()) | (Category.slug == slug)
    )
    if exclude_category_id is not None:
        statement = statement.where(Category.id != exclude_category_id)
    if await session.scalar(statement) is not None:
        raise CategoryAlreadyExistsError


async def list_categories(session: AsyncSession) -> list[Category]:
    """为首页类型 Tag 和发布/编辑文章的分类选项提供统一数据。

    首页用它让用户点击类型后进入对应列表，编辑器用它避免手写不存在的分类。按管理员
    预设的 ``sort_order`` 排列，ID 作为相同排序值时的稳定次序。本函数只读，不提交事务。
    """

    result = await session.scalars(select(Category).order_by(Category.sort_order, Category.id))
    return list(result)


async def get_category_by_id(session: AsyncSession, category_id: int) -> Category | None:
    """确认发布/编辑表单提交的分类 ID 是数据库中真实可关联的分类。

    浏览器提交的 ID 不可信，若直接写入可能触发外键错误或产生无效文章。posts Service 在
    保存前调用本函数；不存在返回 None，再由它抛业务异常并由 Router 显示分类不存在。
    """

    return await session.get(Category, category_id)


async def get_categories_by_ids(session: AsyncSession, category_ids: list[int]) -> list[Category]:
    """批量读取帖子要关联的分类，并按页面展示顺序返回。

    使用单条 ``IN`` 查询避免为每个复选项分别访问数据库。调用方必须比较返回数量与去重后
    的请求数量；数量不一致表示至少一个 ID 不存在，此函数不会用部分结果静默保存帖子。
    """

    statement = (
        select(Category)
        .where(Category.id.in_(category_ids))
        .order_by(Category.sort_order, Category.id)
    )
    result = await session.scalars(statement)
    return list(result)


async def get_category_by_slug(session: AsyncSession, slug: str) -> Category | None:
    """把 URL 中稳定、可读的分类标识转换成 Category。

    页面跳转和筛选使用如 ``python`` 的 slug，而不是可能变化的显示名称。当前主要被默认
    分类查询复用，也为需要取得完整分类对象的 URL 场景提供统一入口；只读且可返回 None。
    """

    return await session.scalar(select(Category).where(Category.slug == slug))


async def get_default_category(session: AsyncSession) -> Category | None:
    """为没有明确分类的可信内部发帖调用提供“其它”分类兜底。

    新发布界面通常要求用户选择分类，但旧测试或内部任务可能未传 ``category_id``。与其写入
    空分类导致列表展示不一致，posts Service 调用这里寻找 slug 为 ``other`` 的预置分类。
    找不到仍返回 None，让创建流程明确失败，而不是静默产生异常数据。
    """

    return await get_category_by_slug(session, DEFAULT_CATEGORY_SLUG)


async def create_category(session: AsyncSession, data: CategoryCreate) -> Category:
    """创建分类并提交事务，成功后返回带数据库 ID 的 ORM 对象。"""

    name = _normalize_name(data.name)
    slug = _normalize_slug(data.slug)
    await _ensure_unique(session, name=name, slug=slug)
    category = Category(name=name, slug=slug, sort_order=data.sort_order)
    session.add(category)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise CategoryAlreadyExistsError from exc
    await session.refresh(category)
    return category


async def update_category(
    session: AsyncSession, category: Category, data: CategoryUpdate
) -> Category:
    """部分更新分类，并在变更后重新加载数据库状态。"""

    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return category

    name = _normalize_name(changes.get("name", category.name))
    slug = _normalize_slug(changes.get("slug", category.slug))
    await _ensure_unique(
        session,
        name=name,
        slug=slug,
        exclude_category_id=category.id,
    )
    category.name = name
    category.slug = slug
    if "sort_order" in changes:
        category.sort_order = changes["sort_order"]
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise CategoryAlreadyExistsError from exc
    await session.refresh(category)
    return category


async def delete_category(session: AsyncSession, category: Category) -> None:
    """删除未被文章引用的分类；被引用时拒绝，避免文章失去分类。"""

    in_use = await session.scalar(
        select(exists().where(post_categories.c.category_id == category.id))
    )
    if in_use:
        raise CategoryInUseError

    await session.delete(category)
    try:
        await session.commit()
    except IntegrityError as exc:
        # 并发请求可能在检查后新增文章分类关联，数据库 RESTRICT 是最终保护。
        await session.rollback()
        raise CategoryInUseError from exc
