"""将 QQ 专用身份字段迁移为通用第三方身份字段。

``provider`` 表示登录平台类型（例如 ``qq`` 或未来的 ``wechat``），
``provider_user_id`` 表示该平台返回的稳定用户 ID。普通密码账号两列都为空。
升级时先复制旧 ``qq_openid`` 数据，再删除旧列；降级遇到非 QQ 身份会拒绝执行，
避免回退过程丢失无法写回旧字段的第三方账号。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_03"
down_revision: str | None = "20260824_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """把旧 QQ 身份数据迁移到复合第三方身份键。"""

    # 先增加新列并建立联合唯一约束。两列可为空，因此普通密码账号不受影响；
    # 约束只会阻止相同平台的同一个用户 ID 绑定多个本站账号。
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("provider", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("provider_user_id", sa.String(length=128), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_users_provider_identity", ["provider", "provider_user_id"]
        )

    # 旧版本只有 QQ，因此所有旧值都明确映射为 provider=qq。
    op.get_bind().execute(
        sa.text(
            "UPDATE users SET provider = 'qq', provider_user_id = qq_openid "
            "WHERE qq_openid IS NOT NULL"
        )
    )

    # 数据复制成功后才删除旧约束和列，降低升级中断造成不可恢复数据的风险。
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_qq_openid", type_="unique")
        batch_op.drop_column("qq_openid")


def downgrade() -> None:
    """仅在所有第三方身份仍为 QQ 时安全恢复旧字段。"""

    connection = op.get_bind()
    non_qq_count = connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM users "
            "WHERE provider_user_id IS NOT NULL "
            "AND (provider IS NULL OR provider <> 'qq')"
        )
    )
    if non_qq_count:
        raise RuntimeError(
            "Cannot downgrade provider identity while non-QQ accounts exist"
        )

    # 先恢复旧列并复制 QQ 身份，再移除新字段；整个过程保留唯一约束保护。
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("qq_openid", sa.String(length=128), nullable=True))
        batch_op.create_unique_constraint("uq_users_qq_openid", ["qq_openid"])

    connection.execute(
        sa.text(
            "UPDATE users SET qq_openid = provider_user_id "
            "WHERE provider = 'qq' AND provider_user_id IS NOT NULL"
        )
    )

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_provider_identity", type_="unique")
        batch_op.drop_column("provider_user_id")
        batch_op.drop_column("provider")
