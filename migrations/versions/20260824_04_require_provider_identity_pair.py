"""要求第三方身份的两个字段必须同时为空或同时有值。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_04"
down_revision: str | None = "20260824_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """阻止 provider 与 provider_user_id 只写一半的无效身份记录。"""

    with op.batch_alter_table("users") as batch_op:
        batch_op.create_check_constraint(
            "ck_users_provider_identity_pair",
            "(provider IS NULL AND provider_user_id IS NULL) "
            "OR (provider IS NOT NULL AND provider_user_id IS NOT NULL)",
        )


def downgrade() -> None:
    """移除成对约束；联合唯一约束仍由 20260824_03 保留。"""

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_provider_identity_pair", type_="check")
