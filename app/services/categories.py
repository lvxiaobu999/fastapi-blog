"""博客分类查询服务。

本模块提供分类读取和帖子写入时的分类校验，不负责 HTTP 参数、模板渲染或分类后台管理。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category

DEFAULT_CATEGORY_SLUG = "other"


async def list_categories(session: AsyncSession) -> list[Category]:
    """按导航排序读取全部分类。"""

    result = await session.scalars(select(Category).order_by(Category.sort_order, Category.id))
    return list(result)


async def get_category_by_id(session: AsyncSession, category_id: int) -> Category | None:
    """按主键读取分类，用于创建或更新帖子时校验外键目标。"""

    return await session.get(Category, category_id)


async def get_category_by_slug(session: AsyncSession, slug: str) -> Category | None:
    """按 URL 使用的 slug 查询分类。"""

    return await session.scalar(select(Category).where(Category.slug == slug))


async def get_default_category(session: AsyncSession) -> Category | None:
    """读取“其它”分类，兼容内部旧调用没有显式分类的帖子创建。"""

    return await get_category_by_slug(session, DEFAULT_CATEGORY_SLUG)
