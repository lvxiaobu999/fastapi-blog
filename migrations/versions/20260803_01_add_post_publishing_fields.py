"""为帖子增加摘要、横图和上下架状态。

本迁移只改变 ``posts`` 表结构，不上传图片，也不修改历史文章正文。历史文章通过
``is_published=true`` 的服务端默认值保持上架，避免部署后现有内容全部从公开页面消失。

Revision ID: 20260803_01
Revises: 20260731_01
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_01"
down_revision: str | None = "20260731_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加摘要、横图和上下架状态；历史文章保持上架。"""

    with op.batch_alter_table("posts") as batch_op:
        batch_op.add_column(sa.Column("summary", sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column("cover_image_url", sa.String(length=500), nullable=True))
        batch_op.add_column(
            sa.Column("is_published", sa.Boolean(), server_default=sa.true(), nullable=False)
        )


def downgrade() -> None:
    """移除发布信息；摘要、横图和下架状态会永久丢失。"""

    with op.batch_alter_table("posts") as batch_op:
        batch_op.drop_column("is_published")
        batch_op.drop_column("cover_image_url")
        batch_op.drop_column("summary")
