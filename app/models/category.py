"""博客分类 ORM 模型。

分类属于可运营数据，允许以后在数据库中新增、排序或停用；本模型不负责帖子筛选和
分类管理接口的业务规则。
"""

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.post import Post


class Category(Base):
    """文章分类，对应 ``categories`` 表。"""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # name 用于页面显示；slug 用于稳定的 URL 查询，避免中文名称变更影响旧链接。
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    # sort_order 只决定二级导航展示顺序，不参与帖子创建时间等业务排序。
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 分类被引用时由数据库 RESTRICT 保护，不允许意外删除后让帖子失去分类。
    posts: Mapped[list["Post"]] = relationship(back_populates="category")
