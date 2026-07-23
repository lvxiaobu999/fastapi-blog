"""支持异步数据库驱动的 Alembic 迁移环境。"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core import get_settings
from app.db.base import Base
from app.models import Post, User  # noqa: F401  # 注册全部模型到 Base.metadata。

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 数据库 URL 从 Settings 读取，不写入 alembic.ini，避免提交生产数据库凭据。
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """不建立数据库连接，只根据 URL 生成迁移 SQL。"""

    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在 AsyncConnection 提供的同步桥接连接中执行 Alembic 操作。"""

    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """创建异步 Engine，并把同步迁移操作交给 run_sync 执行。"""

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """为 Alembic CLI 启动事件循环并执行在线迁移。"""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
