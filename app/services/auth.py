"""密码认证与 JWT 编解码服务；不负责读取 HTTP Header 或决定接口权限。"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from anyio import to_thread
from pwdlib import PasswordHash
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_settings
from app.models import RefreshSession, User

# recommended() 当前选择 Argon2。实例可以安全复用，前导下划线表示其他模块不应绕过
# 本文件提供的 hash_password()/verify_password() 接口直接操作它。
_password_hash = PasswordHash.recommended()

# JWT 是“签名后的身份声明”，不是加密容器；客户端可以读取其中的 sub/iat/exp，
# 但不能在不知道 SECRET_KEY 的情况下伪造有效签名。因此 Token 里只放最小必要信息。


async def hash_password(password: str) -> str:
    """把明文密码转换为适合写入数据库的 Argon2 哈希。

    每次哈希都会包含随机盐，所以同一个明文密码生成的字符串通常不同。调用方只能
    保存返回值，不能保存或记录传入的明文密码。Argon2 属于 CPU 密集计算，因此通过
    AnyIO 工作线程执行，避免阻塞 FastAPI 的 asyncio 事件循环。
    """

    return await to_thread.run_sync(_password_hash.hash, password)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证用户输入的明文密码是否匹配数据库中的 Argon2 哈希。

    这里不能重新哈希明文后比较字符串，因为 Argon2 每次使用随机盐；必须由密码库
    从已有哈希中读取算法参数和盐，再执行验证。返回值只表示是否匹配。
    """

    return await to_thread.run_sync(_password_hash.verify, plain_password, hashed_password)


async def authenticate_user(session: AsyncSession, username: str, password: str) -> User | None:
    """使用用户名或邮箱校验密码；失败统一返回 ``None``，避免泄露账号是否存在。

    ``session`` 由 FastAPI 依赖注入，Service 不自行创建连接，便于测试替换数据库。
    密码验证使用工作线程，因为 Argon2 是有意设计得较慢的 CPU 密集操作；直接在
    asyncio 事件循环执行会阻塞同一进程中的其他请求。
    """

    # OAuth2 表单字段名固定为 username，但这里把它当作“登录标识”，同时匹配用户名
    # 和邮箱。func.lower() 兼容数据库里可能存在的历史混合大小写数据。
    normalized_identity = username.strip().lower()
    user = await session.scalar(
        select(User).where(
            or_(
                func.lower(User.username) == normalized_identity,
                func.lower(User.email) == normalized_identity,
            )
        )
    )
    if user is None:
        return None
    verified = await verify_password(password, user.hashed_password)
    return user if verified else None


def create_access_token(user_id: int, *, expires_delta: timedelta | None = None) -> str:
    """为用户签发带 ``sub``、``iat`` 和 ``exp`` 的短期 JWT。

    ``sub`` 使用数据库主键而不是用户名：用户名将来可能被修改，主键身份更稳定。
    ``expires_delta`` 主要用于过期 Token 测试；正常登录使用 Settings 中的统一时长。
    """

    settings = get_settings()
    issued_at = datetime.now(UTC)
    expires_at = issued_at + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    # PyJWT 会把 datetime 转为 JWT NumericDate。显式保存签发和过期时间，解码时可由
    # 库自动检查 exp，调试时也能解释 Token 为什么失效。
    payload = {"sub": str(user_id), "iat": issued_at, "exp": expires_at}
    return jwt.encode(
        payload,
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )


def verify_access_token(token: str) -> int:
    """验证签名、过期时间和用户主键声明，并返回数据库用户 ID。

    ``algorithms`` 必须由服务端配置提供，不能从 Token Header 读取，否则攻击者可能
    利用算法降级。``require`` 让缺少关键声明的 Token 也被拒绝，而不是当成永久凭据。
    验证成功返回 ``sub`` 对应的整数用户 ID。验证失败时不返回 ``None``，而是保留
    PyJWT 或值转换异常，让依赖层统一转换为 401，同时保留内部可诊断的失败类型。
    """

    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.secret_key.get_secret_value(),
        algorithms=[settings.algorithm],
        options={"require": ["sub", "iat", "exp"]},
    )
    return int(payload["sub"])


def _hash_refresh_token(token: str) -> str:
    """把 Refresh Token 转成数据库可保存的固定长度摘要。

    浏览器 Cookie 保存 ``token`` 明文，服务器数据库只保存 SHA-256 结果。用户刷新时，
    服务端对 Cookie 再做一次相同计算，并用结果查询 ``refresh_sessions.token_hash``。
    这样数据库泄露后，攻击者不能直接把表里的哈希值当成 Refresh Cookie 使用。
    """

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    """把数据库时间统一成带时区 UTC，确保减法和大小比较合法。

    PostgreSQL 的 ``DateTime(timezone=True)`` 通常返回带时区值；SQLite 测试数据库可能
    丢掉 ``tzinfo``。Python 禁止直接比较无时区和有时区时间，所以在过期判断前统一。
    """

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def create_refresh_session(session: AsyncSession, user_id: int) -> str:
    """创建随机 Refresh Token，并记录绝对过期和最近活动时间。

    ``expires_at`` 是本条 Refresh Session 的绝对截止时间；达到它之后，即使用户一直
    操作也不能继续使用。``last_activity_at`` 是最近一次成功认证活动的时间，用来计算
    用户连续多久没有操作。两者分别解决“会话最长多久”和“空闲多久退出”。
    """

    settings = get_settings()
    now = datetime.now(UTC)
    # 高熵随机值只返回给 Router 写入 HttpOnly Cookie，不能写入日志或 JSON 响应。
    raw_token = secrets.token_urlsafe(48)
    session.add(
        RefreshSession(
            user_id=user_id,
            token_hash=_hash_refresh_token(raw_token),
            # 绝对过期：当前实现从创建本条 Session 的时刻开始计算。
            expires_at=now + timedelta(minutes=settings.refresh_token_expire_minutes),
            # 登录就是第一次有效活动，因此初始值与当前时间相同。
            last_activity_at=now,
        )
    )
    await session.commit()
    return raw_token


async def rotate_refresh_session(session: AsyncSession, raw_token: str) -> tuple[int, str] | None:
    """校验 Refresh Cookie，执行空闲/绝对过期检查并轮换 Token。

    轮换表示旧 Token 使用一次后立即标记为撤销，再生成一个新 Token。即使旧 Cookie
    被复制，攻击者之后再使用它也会因 ``revoked=True`` 失败。
    """

    row = await session.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == _hash_refresh_token(raw_token))
    )
    # row=None：Cookie 无法对应数据库会话，可能伪造、已清理或来自其他环境。
    # revoked=True：旧 Token 已被轮换或主动撤销，不能重复使用。
    if row is None or row.revoked:
        return None
    now = datetime.now(UTC)
    settings = get_settings()

    # 绝对过期：当前时间已经到达本条 Session 的固定截止时间。
    absolute_expired = now >= _utc(row.expires_at)

    # 空闲时长 = 当前时间 - 最近活动时间。例如 last_activity_at=10:00、now=10:06，
    # 得到 6 分钟；若配置为 5 分钟，则 idle_expired=True。
    idle_duration = now - _utc(row.last_activity_at)
    idle_timeout = timedelta(minutes=settings.refresh_idle_timeout_minutes)
    idle_expired = idle_duration > idle_timeout

    # ``or`` 表示任一安全边界触发都不能继续刷新：要么总寿命到期，要么空闲过久。
    if absolute_expired or idle_expired:
        row.revoked = True
        await session.commit()
        return None

    # 轮换必须先撤销旧 Session，防止同一 Refresh Token 被重复兑换 Access Token。
    row.revoked = True

    # 注意：当前 create_refresh_session() 会从 now 重新计算 expires_at，因此轮换会延长
    # 绝对期限；若要严格固定“从首次登录起的绝对期限”，新 Session 应继承 row.expires_at。
    new_token = await create_refresh_session(session, row.user_id)
    return row.user_id, new_token


async def touch_refresh_session(session: AsyncSession, raw_token: str, user_id: int) -> bool:
    """在有效的受保护请求中更新最近活动时间，不延长本条 Session 的绝对过期时间。

    本函数由 ``get_current_user()`` 调用。用户每完成一次有效的受保护请求，就把
    ``last_activity_at`` 更新为当前时间，从而实现“持续操作不会因空闲超时退出”。
    返回 False 表示 Cookie 不存在于数据库、属于其他用户、已撤销或已经过期。
    """

    row = await session.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == _hash_refresh_token(raw_token))
    )
    # user_id 必须同时匹配 Access Token 的 sub，避免拿用户 A 的 Refresh Cookie
    # 配合用户 B 的 Access Token 更新错误的会话。
    if row is None or row.revoked or row.user_id != user_id:
        return False
    now = datetime.now(UTC)
    settings = get_settings()

    absolute_expired = now >= _utc(row.expires_at)
    idle_duration = now - _utc(row.last_activity_at)
    idle_timeout = timedelta(minutes=settings.refresh_idle_timeout_minutes)
    idle_expired = idle_duration > idle_timeout

    # 对应原来的复合条件：绝对期限到达或空闲时间超限，任一成立都撤销会话。
    if absolute_expired or idle_expired:
        row.revoked = True
        await session.commit()
        return False

    # 只有两个过期条件都不成立，才把“最近活动时间”向后移动到 now。
    # expires_at 不在这里修改，所以普通业务操作不会延长本条 Session 的绝对期限。
    row.last_activity_at = now
    await session.commit()
    return True
