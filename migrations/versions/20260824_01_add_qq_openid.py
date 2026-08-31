"""为用户增加 QQ 互联 openid 绑定字段。

普通密码账号的 ``qq_openid`` 保持为空；QQ 首次登录时创建的账号会写入 QQ 返回的
openid。字段使用唯一约束，确保一个 QQ 身份不能被绑定到多个博客账号。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_01"
down_revision: str | None = "20260813_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增可为空的 QQ openid 字段及唯一约束，不影响已有用户登录。"""

    # batch_alter_table 同时兼容生产 PostgreSQL 和开发/迁移测试使用的 SQLite；SQLite
    # 不能直接对已存在表执行 ALTER TABLE ADD CONSTRAINT。
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("qq_openid", sa.String(length=128), nullable=True))
        batch_op.create_unique_constraint("uq_users_qq_openid", ["qq_openid"])


def downgrade() -> None:
    """删除 QQ 绑定字段；执行前必须确认不再需要 QQ 登录。"""

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_qq_openid", type_="unique")
        batch_op.drop_column("qq_openid")
