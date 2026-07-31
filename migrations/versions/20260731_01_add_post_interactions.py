"""add post interactions

Revision ID: 20260731_01
Revises: 20260729_02
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260731_01"
down_revision: str | None = "20260729_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加浏览量列以及点赞、收藏、足迹关联表。"""

    with op.batch_alter_table("posts") as batch_op:
        batch_op.add_column(sa.Column("view_count", sa.Integer(), server_default="0", nullable=False))

    for table_name, time_column in (("post_likes", "created_at"), ("post_favorites", "created_at"), ("post_views", "viewed_at")):
        op.create_table(
            table_name,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("post_id", sa.Integer(), nullable=False),
            sa.Column(time_column, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "post_id", name=f"uq_{table_name}_user_post"),
        )
        op.create_index(op.f(f"ix_{table_name}_post_id"), table_name, ["post_id"])
        op.create_index(op.f(f"ix_{table_name}_user_id"), table_name, ["user_id"])


def downgrade() -> None:
    """移除文章互动结构；执行后会丢失互动历史。"""

    for table_name in ("post_views", "post_favorites", "post_likes"):
        op.drop_index(op.f(f"ix_{table_name}_user_id"), table_name=table_name)
        op.drop_index(op.f(f"ix_{table_name}_post_id"), table_name=table_name)
        op.drop_table(table_name)
    with op.batch_alter_table("posts") as batch_op:
        batch_op.drop_column("view_count")
