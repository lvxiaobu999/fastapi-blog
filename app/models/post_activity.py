"""文章点赞、收藏与浏览足迹 ORM 模型。

本模块只保存用户与文章之间的行为关系；文章总浏览量保存在 ``posts.view_count``，
避免每次展示详情时聚合整张足迹表。点赞和收藏的唯一约束保证并发请求不会产生重复关系。
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PostLike(Base):
    """用户对文章的点赞关系。

    一行数据表达“某个用户点赞了某篇文章”，取消点赞就是删除这一行。
    """

    # __tablename__ 决定数据库里的真实表名，SQL 查询最终会访问 post_likes 表。
    __tablename__ = "post_likes"
    # user_id + post_id 必须唯一：同一个用户不能对同一篇文章保存两条点赞记录。
    # 只在 Python 里先查询再插入仍可能遇到并发，因此最终规则必须由数据库保证。
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_post_likes_user_post"),)

    # id 是本行自己的主键。Mapped[int] 是 Python 类型提示，Integer 是数据库列类型。
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # user_id 指向 users.id。用户删除后，CASCADE 让数据库自动清理他的点赞记录。
    # index=True 创建索引，加快“查询某用户赞过什么”的操作。
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # post_id 指向 posts.id；文章删除后，点赞记录也失去意义，因此一起删除。
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    # created_at 记录点赞时间。default 服务于 ORM 写入，server_default 服务于直接 SQL 写入。
    # timezone=True 表示业务代码按带时区时间处理；Python 默认值明确使用 UTC。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class PostFavorite(Base):
    """用户对文章的收藏关系；结构与点赞相似，但表达的是另一种业务动作。"""

    __tablename__ = "post_favorites"
    # 收藏也只能存在一份，重复点击由 Service 解释为取消收藏，而不是新增第二行。
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_post_favorites_user_post"),)

    # 收藏记录自身的主键，不是用户 ID 或文章 ID。
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 两个外键共同回答“谁收藏了哪篇文章”，索引用于个人收藏和文章收藏数查询。
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    # 收藏时间用于“我的收藏”按最近收藏倒序显示。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class PostView(Base):
    """登录用户的文章浏览足迹；同一文章只保留一条并更新最近浏览时间。

    注意：它不是总浏览量明细表。匿名浏览和累计次数保存在 Post.view_count；
    这里仅用于回答“当前登录用户最近看过哪些文章”。
    """

    __tablename__ = "post_views"
    # 唯一约束让同一用户、同一文章始终只有一条足迹，重复浏览只更新时间。
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_post_views_user_post"),)

    # 足迹记录自己的主键。
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 删除用户或文章时，数据库自动清理对应足迹，避免留下无主记录。
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    # viewed_at 是“最近一次浏览时间”，每次登录用户再次打开文章时都会更新。
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
