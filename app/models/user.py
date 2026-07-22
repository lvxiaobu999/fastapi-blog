import secrets

from sqlalchemy import Boolean, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """博客用户。

    普通用户可以登录、管理个人资料，但发帖权限由 ``is_admin`` 控制。
    密码只保存哈希结果，任何 API Schema 都不能暴露 ``hashed_password``。
    """

    __tablename__ = "users"

    # 主键本身已有数据库索引，不再设置 index=True，避免生成重复索引。
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

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

    @property
    def image_path(self) -> str:
        """根据头像文件名生成公开 URL；该派生值不占用数据库字段。"""

        if self.image_file:
            return f"/media/profile_pics/{self.image_file}"
        return "/static/images/default.jpg"
