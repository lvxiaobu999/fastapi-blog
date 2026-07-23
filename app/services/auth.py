"""密码认证与 JWT 编解码服务；不负责读取 HTTP Header 或决定接口权限。"""

from datetime import UTC, datetime, timedelta

import jwt
from anyio import to_thread
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_settings
from app.models import User

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
    """校验用户名和密码；失败统一返回 ``None``，避免泄露账号是否存在。

    ``session`` 由 FastAPI 依赖注入，Service 不自行创建连接，便于测试替换数据库。
    密码验证使用工作线程，因为 Argon2 是有意设计得较慢的 CPU 密集操作；直接在
    asyncio 事件循环执行会阻塞同一进程中的其他请求。
    """

    user = await session.scalar(select(User).where(User.username == username.strip()))
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
