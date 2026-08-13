"""帖子 ORM 模型。

Post 通过 ``user_id`` 外键保存作者身份，通过 ``author`` 关系属性读取完整用户对象；
帖子与分类通过 ``post_categories`` 关联表建立多对多关系。本模块不负责分类存在性校验。
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    # 仅供类型检查器识别，不在运行时导入，避免 Post 与 User 互相导入。
    from app.models.category import Category
    from app.models.comment import Comment
    from app.models.user import User


# 联合主键同时承担唯一约束：同一帖子不能重复关联同一分类。两个外键的删除策略不同，
# 删除帖子时关联记录可自动清理；分类仍被帖子引用时则拒绝删除，避免静默改变文章归类。
post_categories = Table(
    "post_categories",
    Base.metadata,
    Column("post_id", ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "category_id",
        ForeignKey("categories.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    ),
)


class Post(Base):
    """博客帖子，对应数据库中的 ``posts`` 表。"""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    # 摘要用于列表快速介绍文章；允许为空以兼容历史文章，此时前端可回退截取正文。
    summary: Mapped[str | None] = mapped_column(String(300), nullable=True, default=None)
    # 横图由受保护的图片接口保存，这里只持久化站内相对 URL，不把二进制写入数据库。
    cover_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 下架只隐藏文章而不删除数据；历史文章迁移后保持上架，避免升级后全部消失。
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # 总浏览量允许匿名访问累加；server_default 保证历史文章迁移后从 0 开始。
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # Python default 用于 ORM 新增；server_default 用于直接执行 INSERT。
    # timezone=True 表示业务层按带时区时间处理，Python 侧始终生成 UTC 时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    # user_id 是真正存入 posts 表的外键。用户删除时，数据库级联删除其帖子。
    # index=True 可以加快“查询某个用户的所有帖子”这类高频操作。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # author 不是数据库列，而是 SQLAlchemy 根据 user_id 加载出的 User 对象。
    # back_populates 与 User.posts 成对出现，修改任意一侧时 ORM 能同步关系状态。
    author: Mapped["User"] = relationship(back_populates="posts")
    # categories 不是 posts 表中的列。secondary 指向关联表，order_by 保证 API、模板和后台
    # 始终按运营排序输出；passive_deletes 让删除帖子时由数据库级联清理关联记录。
    categories: Mapped[list["Category"]] = relationship(
        secondary=post_categories,
        back_populates="posts",
        order_by="(Category.sort_order, Category.id)",
        passive_deletes=True,
    )
    comments: Mapped[list["Comment"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def category_ids(self) -> list[int]:
        """返回按展示顺序排列的分类 ID，供公开响应直接序列化。"""

        return [category.id for category in self.categories]
