"""允许 QQ-only 账号在主动设置密码前不保存本地密码。

旧版本首次 QQ 登录会写入一个用户不知道的随机密码哈希，只是为了满足 ``users`` 表的
非空约束。新模型把 ``hashed_password`` 改为可空，并将已有 QQ 账号的随机占位哈希清空；
用户之后可以在已认证的 QQ 会话中主动设置本地密码。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_02"
down_revision: str | None = "20260824_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """允许密码哈希为空，并清理旧 QQ 账号的随机占位哈希。"""

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "hashed_password",
            existing_type=sa.String(length=255),
            nullable=True,
        )

    # 只有已经绑定 QQ openid 的账号才是旧 QQ 登录账号；普通密码账号必须保留原哈希。
    op.get_bind().execute(
        sa.text(
            "UPDATE users SET hashed_password = NULL "
            "WHERE qq_openid IS NOT NULL"
        )
    )


def downgrade() -> None:
    """恢复密码非空约束；存在 QQ-only 账号时拒绝有损回退。"""

    connection = op.get_bind()
    missing_count = connection.scalar(
        sa.text("SELECT COUNT(*) FROM users WHERE hashed_password IS NULL")
    )
    if missing_count:
        raise RuntimeError(
            "Cannot downgrade passwordless QQ users; set a password for every QQ account first"
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "hashed_password",
            existing_type=sa.String(length=255),
            nullable=False,
        )
