"""帖子 ORM 模型。

Post 通过 ``user_id`` 外键保存作者身份，通过 ``author`` 关系属性读取完整用户对象。
一个用户可以拥有多篇帖子，每篇帖子只能属于一个用户。
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    # 仅供类型检查器识别，不在运行时导入，避免 Post 与 User 互相导入。
    from app.models.user import User


class Post(Base):
    """博客帖子，对应数据库中的 ``posts`` 表。"""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

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
