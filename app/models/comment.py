"""评论 ORM 模型。

本模块只描述评论的持久化结构与关联；实时连接和广播由 WebSocket 层负责。
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.post import Post
    from app.models.user import User


class Comment(Base):
    """用户发表在指定帖子下的评论。"""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    # 一条评论只属于一篇帖子。帖子被删除后，评论失去展示上下文，因此一起删除。
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 作者身份只从认证后的服务端用户写入，不能接受前端提交的 user_id。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # parent_id 指向实际被回复的评论；root_id 固定指向所属顶级评论。
    # 顶级评论的两个字段都为空，回复则两个字段都非空，便于二级平铺展示。
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    root_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True
    )

    post: Mapped["Post"] = relationship(back_populates="comments")
    author: Mapped["User"] = relationship(back_populates="comments")
    # Comment 同时有 parent_id 和 root_id 两个指向 comments.id 的外键，因此必须用
    # foreign_keys 明确说明 parent 关系使用哪一个。remote_side=[id] 表示 id 是父级一侧。
    parent: Mapped["Comment | None"] = relationship(foreign_keys=[parent_id], remote_side=[id])

    @property
    def reply_to_author(self) -> "User | None":
        """返回实际被回复评论的作者，供公开响应显示 @昵称。"""

        return self.parent.author if self.parent is not None else None
