"""幂等写入博客首次运行所需的默认文章分类。

Revision ID: 20260810_01
Revises: c56c4d6eeba7
Create Date: 2026-08-10

本迁移只补缺失分类，不覆盖运营人员已修改的名称或排序，也不删除已有分类。它不能修复
早期 ``145903a6328c`` 升级前已经存在文章却没有 ``category_id`` 的历史数据；该场景仍需
先按迁移文档回填数据，再继续升级。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_01"
down_revision: str | None = "c56c4d6eeba7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_CATEGORIES = (
    {"name": "FastAPI", "slug": "fastapi", "sort_order": 10},
    {"name": "Python", "slug": "python", "sort_order": 20},
    {"name": "其它", "slug": "other", "sort_order": 30},
)


def upgrade() -> None:
    """只插入数据库中尚不存在的默认 slug，重复执行不会产生重复行。

    迁移文件使用临时 ``sa.table`` 描述需要操作的列，不导入应用 ORM Model。这样历史迁移不会
    因未来 Model 再次变化而改变含义，SQLite 与 PostgreSQL 也能复用同一段查询和插入逻辑。
    """

    categories = sa.table(
        "categories",
        sa.column("name", sa.String(length=50)),
        sa.column("slug", sa.String(length=50)),
        sa.column("sort_order", sa.Integer()),
    )
    connection = op.get_bind()
    existing_slugs = set(
        connection.execute(
            sa.select(categories.c.slug).where(
                categories.c.slug.in_([item["slug"] for item in DEFAULT_CATEGORIES])
            )
        ).scalars()
    )
    # 先查询再补缺失项，保留管理员已经创建或修改过的同 slug 分类；若名称唯一约束存在冲突，
    # 迁移会明确失败，提醒维护者检查数据，而不是静默覆盖运营数据。
    missing = [item for item in DEFAULT_CATEGORIES if item["slug"] not in existing_slugs]
    if missing:
        op.bulk_insert(categories, missing)


def downgrade() -> None:
    """保留分类数据，避免删除已被文章引用或由运营人员修改的记录。"""

    # 数据迁移无法可靠区分“本迁移插入”与“此前同 slug 已存在”的分类。降级采用无损策略；
    # 后续结构迁移会在删除 categories 表时统一处理，不在此处破坏业务数据。
