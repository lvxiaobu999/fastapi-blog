"""删除数据库 Refresh Session 表，改由 Redis 保存有状态会话。

升级会永久删除表内现有会话，因此部署后所有用户都需要重新登录。用户、帖子和其他业务
数据不受影响。降级只恢复旧表结构，无法从 Redis 反向恢复迁移前已经存在的会话数据。

Revision ID: c56c4d6eeba7
Revises: 20260803_01
Create Date: 2026-08-07 12:22:32.814887
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c56c4d6eeba7"
down_revision: str | None = "20260803_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """删除旧索引与会话表；Redis Key TTL 接管后续过期清理。"""

    op.drop_index("ix_refresh_sessions_token_hash", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")


def downgrade() -> None:
    """恢复旧表结构；仅用于代码回退，不恢复迁移前会话数据。"""

    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"], unique=False)
    op.create_index(
        "ix_refresh_sessions_token_hash", "refresh_sessions", ["token_hash"], unique=True
    )
