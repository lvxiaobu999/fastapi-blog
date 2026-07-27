"""Refresh Token 服务端会话模型。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RefreshSession(Base):
    """保存 Refresh Token 的服务端会话状态。

    Access Token 是无状态 JWT，而 Refresh Token 需要服务端状态才能实现轮换、撤销、
    空闲超时和绝对过期。数据库只保存 Refresh Token 的哈希，不保存浏览器 Cookie 中的
    原始 Token；即使表数据泄露，哈希值也不能直接作为 Cookie 使用。
    """

    # 表名使用复数，与 users/posts 命名方式保持一致。每次登录和 Refresh Token 轮换
    # 都会产生一条记录，旧记录保留 revoked 状态，便于判断旧 Token 是否被重复使用。
    __tablename__ = "refresh_sessions"

    # 会话记录的数据库主键。它只用于数据库内部定位，不写入 Access Token 或 Cookie。
    # Integer primary key 自带主键索引，因此无需再设置 index=True。
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Refresh Session 所属用户。登录成功时由 authenticate_user() 得到 User.id，随后
    # create_refresh_session() 写入该字段。ondelete="CASCADE" 表示删除用户时数据库同时
    # 删除其所有 Refresh Session，避免留下无法再关联用户的认证记录。
    # index=True 加速“查询某个用户的全部会话、撤销所有设备登录”等后续功能。
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # 浏览器持有原始 Refresh Token，数据库只保存 SHA-256 后的 64 位十六进制字符串。
    # 刷新请求会对 Cookie 做相同哈希，再按本字段查询。unique=True 保证一个 Token 只
    # 对应一条会话；index=True 让每次刷新和活动更新无需扫描整张表。
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # 本条 Refresh Session 的绝对截止时间。到达该时间后，即使用户一直有操作也应要求
    # 重新登录。timezone=True 表示业务层按带时区时间处理；Service 比较前会统一为 UTC。
    # nullable=False 保证每条会话都有明确上限，不能意外产生永久 Refresh Token。
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # 最近一次成功认证活动的 UTC 时间，用来实现“连续多久没有操作才退出”。登录时
    # 初始化为当前时间；受保护请求通过 touch_refresh_session() 更新。Service 使用
    # now - last_activity_at 计算空闲时长，并与 REFRESH_IDLE_TIMEOUT_MINUTES 比较。
    # 更新本字段只移动空闲窗口，不应该修改 expires_at 的绝对上限。
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # 服务端撤销标记。False 表示会话仍可能使用；True 表示旧 Token 已轮换、已退出、
    # 已过期或被管理员撤销。不能直接删除旧行替代此字段，否则无法区分“从未存在的
    # Token”和“已经使用过的 Token”，也不利于后续做重放检测或安全审计。
    # default=False 是 Python/ORM 新建对象时的默认值，数据库列仍要求非空。
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ORM 关系属性，不是 refresh_sessions 表中的新列。通过 session.user 可以访问所属
    # User；back_populates 与 User.refresh_sessions 成对定义，使双向关系状态保持同步。
    user = relationship("User", back_populates="refresh_sessions")
