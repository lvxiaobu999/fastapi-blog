"""基于 Redis 一次性验证码的忘记密码业务。

验证码和重发冷却只保存在 Redis，不新增用户表字段，因此不需要 Alembic 迁移。
Redis 中只保存验证码的 HMAC 摘要与用户 ID；邮件正文中的明文验证码不会写入日志或数据库。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_settings
from app.models import User
from app.services import auth as auth_service
from app.services import email as email_service
from app.services import refresh_sessions as refresh_session_service

_CONFIRM_LOCK_TTL_SECONDS = 30


class PasswordResetCodeError(ValueError):
    """验证码不存在、过期、格式无效或尝试次数过多。"""


def _normalize_email(email: str) -> str:
    """统一邮箱空格与大小写，确保查询和 Redis Key 使用同一身份。

    例如 ``" User@Example.com "`` 会变成 ``"user@example.com"``。这一步必须在
    查询数据库、生成摘要、拼接 Redis Key 之前完成，否则同一个邮箱的不同写法会
    被误认为不同账号，用户可能收不到或无法使用验证码。
    """

    return email.strip().lower()


def _email_digest(email: str) -> str:
    """生成不暴露原始邮箱的 Redis Key 片段。

    这里使用 SHA-256 的目的不是加密邮箱，而是把邮箱转换为固定长度、不可读的
    Key 片段。Redis 运维人员看到 Key 时不会直接看到用户邮箱；同一个标准化邮箱
    每次仍会得到同一个摘要，因而能够稳定定位它的验证码记录。
    """

    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def _key(email: str, kind: str) -> str:
    """生成密码重置功能使用的 Redis Key。

    ``kind`` 表示同一个邮箱下的不同用途：

    * ``"cooldown"``：重发冷却锁，防止 60 秒内重复发邮件；
    * ``"code"``：验证码 JSON 记录，保存用户 ID、验证码摘要和错误次数；
    * ``"confirm-lock"``：确认请求的短时并发锁，防止同一验证码被同时消费。

    Key 结构为：

    ``<REDIS_KEY_PREFIX>:<ENV>:password-reset:<kind>:<邮箱 SHA-256>``

    假设配置为 ``REDIS_KEY_PREFIX=fastapi-blog``、``ENV=development``，输入
    ``" User@Example.com "``，标准化后是 ``user@example.com``，其 SHA-256
    为 ``b4c9a289323b21a01c3e940f150eb9b8c542587f1abfd8f0e1cc1ffc5e475514``，
    那么调用 ``_key(normalized_email, "cooldown")`` 会得到类似：

    ``fastapi-blog:development:password-reset:cooldown:b4c9a289...``

    实际 Key 会包含完整 64 位摘要；这里在说明中省略中间部分只是为了便于阅读。
    ``cooldown``、``code`` 和 ``confirm-lock`` 只有用途不同，邮箱部分相同，因此
    三个 Key 能够互相对应，又不会彼此覆盖。

    注意：如果环境文件把 ``REDIS_KEY_PREFIX`` 本身写成了
    ``fastapi-blog:development``，而 ``ENV=development``，最终会出现
    ``fastapi-blog:development:development:...``。这是配置值和环境值各自参与拼接的结果，
    不代表函数把同一参数错误拼接了两次。
    """

    settings = get_settings()
    return (
        f"{settings.redis_key_prefix}:{settings.env}:password-reset:{kind}:{_email_digest(email)}"
    )


def _code_digest(email: str, code: str) -> str:
    """使用应用 Secret 对验证码做 HMAC，避免 Redis 泄露后可直接得到验证码。

    摘要输入是 ``标准化邮箱:验证码``，密钥是应用的 ``SECRET_KEY``。确认时用同样
    的规则重新计算摘要，再通过 ``hmac.compare_digest`` 比较；Redis 中只保存摘要，
    不保存邮件正文中的 6 位明文验证码。
    """

    settings = get_settings()
    secret = settings.secret_key.get_secret_value().encode("utf-8")
    return hmac.new(secret, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _new_code() -> str:
    """生成六位数字验证码；前导零也必须保留。

    ``randbelow(1_000_000)`` 产生 0 到 999999 的安全随机数，``:06d`` 将其
    格式化为固定六位，例如数值 ``123`` 会变成字符串 ``"000123"``。验证码必须
    作为字符串传递和发送，不能转成整数，否则前导零会丢失。
    """

    return f"{secrets.randbelow(1_000_000):06d}"


async def request_password_reset(
    session: AsyncSession,
    redis: Redis,
    email: str,
) -> None:
    """为邮箱创建验证码并发送邮件；邮箱不存在时静默返回以避免账号枚举。"""

    normalized_email = _normalize_email(email)
    settings = get_settings()
    # 一个邮箱对应三类不同 Key。请求验证码阶段只先操作 cooldown_key，只有
    # 冷却锁抢到手（SET ... NX 成功）后才允许继续查库、生成验证码和连接 SMTP。
    cooldown_key = _key(normalized_email, "cooldown")
    reset_key = _key(normalized_email, "code")

    # NX + EX 让并发请求最多创建一个冷却窗口，重复点击不重复发信。
    # 返回 True 表示当前请求创建了 Key；False 表示 Key 已存在，说明仍在冷却期。
    acquired = await redis.set(
        cooldown_key,
        "1",
        ex=settings.password_reset_resend_interval_seconds,
        nx=True,
    )
    if not acquired:
        return

    user = await session.scalar(select(User).where(func.lower(User.email) == normalized_email))
    if user is None:
        # 即使邮箱不存在也保留冷却窗口，降低通过接口进行邮件/数据库探测的风险。
        return
    if (
        user.provider == "qq"
        and user.provider_user_id
        and normalized_email.endswith("@qq-accounts.internal")
    ):
        # QQ 首次登录生成的是不可投递的内部占位邮箱。保持与“邮箱不存在”相同的静默
        # 响应，既不尝试向伪地址发信，也不暴露账号类型；用户需先在个人资料绑定真实邮箱。
        return

    code = _new_code()
    # 邮件里需要明文 code，但 Redis 只保存摘要。payload 序列化为 JSON 是因为
    # Redis String 只能保存文本；TTL 到期后 Redis 自动删除整条记录。
    payload = {
        "user_id": user.id,
        "email_digest": _email_digest(normalized_email),
        "code_digest": _code_digest(normalized_email, code),
        "attempts": 0,
    }
    await redis.set(
        reset_key,
        json.dumps(payload, separators=(",", ":")),
        ex=settings.password_reset_code_ttl_seconds,
    )
    try:
        # SMTP 是同步库，email Service 内部会切到 AnyIO 工作线程；只有邮件发送成功，
        # 用户才真正能拿到验证码。发送失败时下面的补偿逻辑会删除两个 Key，允许重试。
        await email_service.send_password_reset_email(normalized_email, code)
    except email_service.EmailDeliveryError:
        # 邮件发送失败时允许用户稍后重试，并删除尚未送达的验证码。
        await redis.delete(reset_key, cooldown_key)
        raise


def _invalid_code() -> PasswordResetCodeError:
    """返回统一错误，避免暴露“邮箱不存在/验证码过期/用户已删除”等内部状态。"""

    return PasswordResetCodeError("Invalid or expired verification code")


async def _load_record(redis: Redis, email: str) -> tuple[str, dict[str, Any], int]:
    """读取验证码记录、解析 JSON 并返回剩余 TTL。

    返回的三项分别是 ``(Redis Key, 已校验的 payload, 剩余秒数)``。剩余 TTL 会在
    错误次数更新时原样传回 Redis，避免用户输错一次验证码后有效期被意外延长。
    """

    key = _key(email, "code")
    raw = await redis.get(key)
    # Key 不存在通常代表验证码过期、已经成功使用，或错误次数已达到上限；这些情况
    # 对外统一成同一条错误消息，避免暴露内部状态。
    if raw is None:
        raise _invalid_code()
    try:
        payload = json.loads(raw)
        user_id = int(payload["user_id"])
        email_digest = str(payload["email_digest"])
        code_digest = str(payload["code_digest"])
        attempts = int(payload["attempts"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        await redis.delete(key)
        raise _invalid_code() from exc
    if user_id <= 0 or not email_digest or not code_digest or attempts < 0:
        await redis.delete(key)
        raise _invalid_code()
    # Redis TTL 可能返回 -1（没有过期时间）或 -2（Key 已不存在），两者都不能继续
    # 用作密码重置凭据，因此必须拒绝并清理异常记录。
    ttl = await redis.ttl(key)
    if ttl <= 0:
        await redis.delete(key)
        raise _invalid_code()
    return (
        key,
        {
            "user_id": user_id,
            "email_digest": email_digest,
            "code_digest": code_digest,
            "attempts": attempts,
        },
        ttl,
    )


async def confirm_password_reset(
    session: AsyncSession,
    redis: Redis,
    *,
    email: str,
    code: str,
    new_password: str,
) -> None:
    """校验验证码、更新 Argon2 密码并撤销用户全部 Refresh Session。"""

    normalized_email = _normalize_email(email)
    # 确认接口增加 30 秒短锁。验证码校验不是天然的单次原子操作，如果两个请求同时
    # GET 同一条记录，可能都通过校验；短锁让同一邮箱在这段时间内只有一个确认请求。
    lock_key = _key(normalized_email, "confirm-lock")
    locked = await redis.set(lock_key, "1", ex=_CONFIRM_LOCK_TTL_SECONDS, nx=True)
    if not locked:
        # 同一邮箱的并发确认只允许一个请求继续，避免两个请求同时消费同一验证码。
        raise _invalid_code()

    try:
        key, payload, ttl = await _load_record(redis, normalized_email)
        # expected 只存在于当前 Python 请求内，不写入 Redis、不写日志，也不返回前端。
        expected = _code_digest(normalized_email, code)
        if not hmac.compare_digest(payload["code_digest"], expected):
            settings = get_settings()
            attempts = payload["attempts"] + 1
            if attempts >= settings.password_reset_max_attempts:
                # 达到最大失败次数后直接删除，攻击者不能继续猜测剩余验证码。
                await redis.delete(key)
            else:
                # 保留原 TTL，只增加 attempts；输错验证码不会获得新的 10 分钟窗口。
                payload["attempts"] = attempts
                await redis.set(key, json.dumps(payload, separators=(",", ":")), ex=ttl)
            raise _invalid_code()

        if payload["email_digest"] != _email_digest(normalized_email):
            await redis.delete(key)
            raise _invalid_code()
        user = await session.get(User, payload["user_id"])
        if user is None or _normalize_email(user.email) != normalized_email:
            await redis.delete(key)
            raise _invalid_code()

        # 先删除验证码和冷却 Key，使验证码立即失效且不能重放；再哈希新密码、撤销
        # 所有 Refresh Session，最后提交数据库。Redis 在前面失败时不会写入新密码。
        await redis.delete(key, _key(normalized_email, "cooldown"))
        user.hashed_password = await auth_service.hash_password(new_password)
        await refresh_session_service.revoke_user_refresh_sessions(redis, user.id)
        await session.commit()
    finally:
        # 清理锁失败时让 TTL 自动释放；如果数据库已经提交，不能把成功误报成 503。
        try:
            await redis.delete(lock_key)
        except RedisError:
            pass
