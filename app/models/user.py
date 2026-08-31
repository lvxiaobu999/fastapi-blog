import secrets
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Integer, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    # 仅用于静态类型提示，避免 User 与 Post 在运行时循环导入。
    from app.models.comment import Comment
    from app.models.post import Post


class User(Base):
    """博客用户。

    普通用户可以登录、管理个人资料，但发帖权限由 ``is_admin`` 控制。
    密码只保存哈希结果，任何 API Schema 都不能暴露 ``hashed_password``。QQ-only 账号在
    用户主动设置本地密码前允许该字段为空；这表示只能通过 QQ 登录，不代表保存了空密码。
    """

    __tablename__ = "users"
    # 第三方身份的唯一性是两个字段的组合，而不是 provider_user_id 单列唯一；
    # 不同平台可能返回相同文本 ID，联合约束允许它们分别绑定不同本站账号。
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_user_id", name="uq_users_provider_identity"
        ),
        CheckConstraint(
            "(provider IS NULL AND provider_user_id IS NULL) "
            "OR (provider IS NOT NULL AND provider_user_id IS NOT NULL)",
            name="ck_users_provider_identity_pair",
        ),
    )

    # 主键本身已有数据库索引，不再设置 index=True，避免生成重复索引。
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 第三方身份拆成“提供商 + 提供商用户 ID”两个字段：QQ 使用 provider="qq"，微信可以
    # 使用 provider="wechat"。两列允许为空是因为普通密码账号没有第三方身份；联合唯一约束
    # 由迁移保证，避免同一个第三方身份绑定到多个博客账号。
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    @property
    def has_password(self) -> bool:
        """返回是否已经设置本地密码；只暴露布尔状态，不暴露密码哈希。"""

        return self.hashed_password is not None

    # nickname 不要求唯一。注册接口未提供昵称时，用随机值保证数据库中的字段始终非空；
    # 这里的 default 是 Python 侧默认值，只在 SQLAlchemy 执行 INSERT 时生效。
    nickname: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=lambda: f"user_{secrets.token_hex(4)}",
    )

    # 数据库只保存头像文件的存储名称，不重复保存可由它推导出的 URL。
    image_file: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)

    # 同时设置 Python 默认值和数据库默认值：ORM 新增与直接执行 INSERT 都默认为普通用户。
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )

    # 一个用户拥有多篇帖子。delete-orphan 表示帖子离开其唯一作者后不能独立存在；
    # passive_deletes=True 让数据库根据 Post.user_id 的 ON DELETE CASCADE 完成级联删除。
    posts: Mapped[list["Post"]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    comments: Mapped[list["Comment"]] = relationship(
        back_populates="author", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def image_path(self) -> str:
        """根据头像文件名生成公开 URL；该派生值不占用数据库字段。"""

        if self.image_file:
            return f"/media/profile_pics/{self.image_file}"
        return "/static/images/default.jpg"
