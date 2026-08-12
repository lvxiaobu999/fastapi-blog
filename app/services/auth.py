"""密码认证与 JWT 编解码服务；不负责读取 HTTP Header 或决定接口权限。"""

from datetime import UTC, datetime, timedelta

import jwt
from anyio import to_thread
from pwdlib import PasswordHash
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_settings
from app.models import User

# ==================== Service 入口导读 ====================
# 上游调用者：api_auth Router、认证 Depends、users Service。
# 本模块负责密码哈希/校验、登录身份查询和 Access JWT。Redis Refresh Session 生命周期
# 独立放在 services/refresh_sessions.py，避免 JWT 编解码与有状态会话存储混在一起。
# 它不知道 HTTP Header、Cookie 或状态码；Router/Depends 把返回值或异常翻译成 HTTP。
# 密码计算会切到工作线程；Refresh Session 写操作使用调用方传入的 AsyncSession。

# recommended() 当前选择 Argon2。实例可以安全复用，前导下划线表示其他模块不应绕过
# 本文件提供的 hash_password()/verify_password() 接口直接操作它。
_password_hash = PasswordHash.recommended()

# 登录失败必须尽量保持相同的密码计算成本。用户不存在时也校验这条固定 Argon2 哈希，
# 避免攻击者通过响应耗时区分“账号不存在”和“密码错误”。该值不是任何真实账号的密码，
# 也不参与登录成功判断；若以后调整密码哈希算法，应同步生成相同算法的新占位哈希。
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$FZWKEj8K9h7s4Y2y+iHGzw$"
    "PB7t+fH2+PGerWWsn2I+z+GWNoVLjFEsSBOtp/UM4Q0"
)

# JWT 是“签名后的身份声明”，不是加密容器；客户端可以读取其中的 sub/iat/exp，
# 但不能在不知道 SECRET_KEY 的情况下伪造有效签名。因此 Token 里只放最小必要信息。


async def hash_password(password: str) -> str:
    """把明文密码转换为适合写入数据库的 Argon2 哈希。

    用户注册、管理员创建账号和修改密码都需要保存密码，但产品不能在数据库泄露时暴露
    用户明文密码，因此这些入口统一先调用本函数，数据库只接收不可逆哈希。

    每次哈希都会包含随机盐，所以同一个明文密码生成的字符串通常不同。调用方只能
    保存返回值，不能保存或记录传入的明文密码。Argon2 属于 CPU 密集计算，因此通过
    AnyIO 工作线程执行，避免阻塞 FastAPI 的 asyncio 事件循环。
    """

    return await to_thread.run_sync(_password_hash.hash, password)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证用户输入的明文密码是否匹配数据库中的 Argon2 哈希。

    登录需要证明操作者知道密码，修改密码还需要再次验证旧密码；这两个用户操作都不能
    直接读取原密码（数据库没有保存它），所以使用密码库验证输入与已有哈希是否对应。

    这里不能重新哈希明文后比较字符串，因为 Argon2 每次使用随机盐；必须由密码库
    从已有哈希中读取算法参数和盐，再执行验证。返回值只表示是否匹配。
    """

    return await to_thread.run_sync(_password_hash.verify, plain_password, hashed_password)


async def authenticate_user(session: AsyncSession, username: str, password: str) -> User | None:
    """使用用户名或邮箱校验密码；失败统一返回 ``None``，避免泄露账号是否存在。

    它对应登录窗口点击“登录”后的核心身份确认：用户既可以填 username，也可以填 email，
    成功后 Router 才能签发 Token、关闭登录窗口并显示头像；失败则显示统一登录错误。

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
        await verify_password(password, _DUMMY_PASSWORD_HASH)
        return None
    verified = await verify_password(password, user.hashed_password)
    return user if verified else None


def create_access_token(user_id: int, *, expires_delta: timedelta | None = None) -> str:
    """为用户签发带 ``sub``、``iat`` 和 ``exp`` 的短期 JWT。

    登录成功或刷新会话成功后，前端需要一张短期“身份凭证”访问发帖、评论、点赞等受保护
    接口。Access Token 正是这张凭证；短期过期可降低泄露风险，过期后由 Refresh 流程续签。

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

    每个受保护请求都必须先回答“这张凭证是否真实且仍有效”。该函数是认证 Depends 和评论
    WebSocket 认证的共同底层步骤；无效或过期最终转成 401，前端据此清理假登录状态并弹窗。

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
