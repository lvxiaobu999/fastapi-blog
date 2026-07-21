"""数据库连接与会话配置。

本模块只负责创建 SQLAlchemy Engine、Session 工厂和 FastAPI 数据库依赖，
不会在导入时创建数据表。Engine 本身采用延迟连接，第一次真正执行 SQL 时才连接数据库。
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import get_settings

settings = get_settings()

# SQLite 与 PostgreSQL 的驱动参数不同，因此先判断当前连接是否为 SQLite。
is_sqlite = settings.database_url.startswith("sqlite:")

engine = create_engine(
    # SQLAlchemy 根据 URL 前缀选择数据库方言和驱动：开发环境是 SQLite，生产环境是 PostgreSQL。
    settings.database_url,
    # SQLite 默认限制一个连接只能在创建它的线程中使用。
    # FastAPI 的同步请求可能在线程池中的不同线程执行，所以开发环境关闭该限制；
    # PostgreSQL 驱动没有这个参数，因此生产环境传入空字典。
    connect_args={"check_same_thread": False} if is_sqlite else {},
    # 从连接池取出连接前先做存活检测，失效连接会被丢弃并重新建立。
    # 这能减少 PostgreSQL 长连接被服务端断开后，第一次请求直接失败的情况。
    pool_pre_ping=True,
)

# sessionmaker 创建的是“Session 工厂”，SessionLocal 本身不是数据库会话。
# 每次调用 SessionLocal() 才会生成一个独立 Session：
#
# - bind=engine：让该 Session 通过上面的 Engine 获取连接并执行 SQL。
# - autoflush=False：执行查询前不自动把内存中的新增/修改同步到数据库。
#   这样写入时机更显式；需要在 commit() 前获取数据库生成值时，应主动调用 flush()。
#   如果设为 True（SQLAlchemy 默认值），查询前可能自动发出 INSERT/UPDATE，但仍不会提交事务。
# - expire_on_commit=False：commit() 后保留对象已经加载的属性值，返回响应时通常无需再次查询。
#   如果设为 True（SQLAlchemy 默认值），提交后对象属性会被标记为“过期”；下次访问属性时，
#   SQLAlchemy 会尝试重新查询数据库以获得最新值。如果 Session 已关闭，此时访问过期属性可能抛出
#   DetachedInstanceError；如果 Session 仍开启，则会产生额外 SQL 查询。
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Iterator[Session]:
    """为一次 FastAPI 请求提供独立 Session，并在请求结束后自动关闭。"""

    # with 会在正常返回或发生异常时关闭 Session，把连接归还连接池；
    # 关闭 Session 不等于提交事务，写操作仍需在 Service 中显式调用 commit()。
    with SessionLocal() as session:
        yield session
