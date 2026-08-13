"""博客分类查询服务。

本模块提供分类读取和帖子写入时的分类校验，不负责 HTTP 参数、模板渲染或分类后台管理。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category

# ==================== Service 入口导读 ====================
# 上游调用者：pages Router 和 posts Service。
# 本模块只读取分类，不处理 HTTP、权限或模板；list 用于首页/编辑器，ID/slug 用于文章校验。
# 所有函数都是只读查询，因此不会 add、delete 或 commit；找不到时返回 None 交给上游处理。

DEFAULT_CATEGORY_SLUG = "other"


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


async def get_categories_by_ids(
    session: AsyncSession, category_ids: list[int]
) -> list[Category]:
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
