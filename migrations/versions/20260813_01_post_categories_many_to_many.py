"""将帖子与分类从单分类外键迁移为多对多关联表。

Revision ID: 20260813_01
Revises: 20260810_01
Create Date: 2026-08-13

升级会完整保留每篇历史帖子的原分类。降级只能从多个分类中保留 ID 最小的一项，因此属于
有损回退；执行 downgrade 前必须先备份并确认可以丢弃额外分类关系。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_01"
down_revision: str | None = "20260810_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建关联表、复制历史分类关系，再删除旧单分类列。"""

    op.create_table(
        "post_categories",
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("post_id", "category_id"),
    )
    # 联合主键以 post_id 开头，不能高效支撑“按分类找帖子”，因此补反向索引。
    op.create_index(
        op.f("ix_post_categories_category_id"),
        "post_categories",
        ["category_id"],
        unique=False,
    )

    post_categories = sa.table(
        "post_categories",
        sa.column("post_id", sa.Integer()),
        sa.column("category_id", sa.Integer()),
    )
    posts = sa.table(
        "posts",
        sa.column("id", sa.Integer()),
        sa.column("category_id", sa.Integer()),
    )
    # 先复制再删列，迁移中途失败时不会丢失历史文章分类。
    op.get_bind().execute(
        post_categories.insert().from_select(
            ["post_id", "category_id"], sa.select(posts.c.id, posts.c.category_id)
        )
    )

    # batch 模式兼容开发环境 SQLite；PostgreSQL 会生成等价的 ALTER TABLE。
    with op.batch_alter_table("posts") as batch_op:
        batch_op.drop_constraint("fk_posts_category_id_categories", type_="foreignkey")
        batch_op.drop_index(op.f("ix_posts_category_id"))
        batch_op.drop_column("category_id")


def downgrade() -> None:
    """恢复单分类列；多分类帖子只保留分类 ID 最小的一项。"""

    with op.batch_alter_table("posts") as batch_op:
        # 必须先允许 NULL，完成数据回填后才能恢复非空约束。
        batch_op.add_column(sa.Column("category_id", sa.Integer(), nullable=True))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE posts SET category_id = ("
            "SELECT MIN(post_categories.category_id) FROM post_categories "
            "WHERE post_categories.post_id = posts.id)"
        )
    )

    missing_count = connection.scalar(
        sa.text("SELECT COUNT(*) FROM posts WHERE category_id IS NULL")
    )
    if missing_count:
        # 正常应用写入保证至少一个分类；若数据库被手工改坏，使用预置“其它”分类兜底。
        default_category_id = connection.scalar(
            sa.text("SELECT id FROM categories WHERE slug = 'other'")
        )
        if default_category_id is None:
            raise RuntimeError("Cannot downgrade posts without categories: default category missing")
        connection.execute(
            sa.text("UPDATE posts SET category_id = :category_id WHERE category_id IS NULL"),
            {"category_id": default_category_id},
        )

    with op.batch_alter_table("posts") as batch_op:
        batch_op.alter_column("category_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(op.f("ix_posts_category_id"), ["category_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_posts_category_id_categories",
            "categories",
            ["category_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.drop_index(op.f("ix_post_categories_category_id"), table_name="post_categories")
    op.drop_table("post_categories")
