"""add comment replies

Revision ID: 20260729_02
Revises: 20260729_01
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_02"
down_revision: str | None = "20260729_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加实际回复目标和顶级评论分组字段。"""

    with op.batch_alter_table("comments") as batch_op:
        batch_op.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("root_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_comments_parent_id_comments", "comments", ["parent_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_foreign_key(
            "fk_comments_root_id_comments", "comments", ["root_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index("ix_comments_parent_id", ["parent_id"], unique=False)
        batch_op.create_index("ix_comments_root_id", ["root_id"], unique=False)


def downgrade() -> None:
    """移除回复关系；评论正文仍保留，但会全部退化为顶级评论。"""

    with op.batch_alter_table("comments") as batch_op:
        batch_op.drop_index("ix_comments_root_id")
        batch_op.drop_index("ix_comments_parent_id")
        batch_op.drop_constraint("fk_comments_root_id_comments", type_="foreignkey")
        batch_op.drop_constraint("fk_comments_parent_id_comments", type_="foreignkey")
        batch_op.drop_column("root_id")
        batch_op.drop_column("parent_id")
