"""使用 Redis 管理 Refresh Token 的创建、轮换与撤销。

初学者可以先记住本模块在认证系统中的位置：

1. ``services/auth.py`` 负责验证密码和签发短期 Access JWT；
2. 本模块负责保存长期一些、能够续签 Access JWT 的 Refresh Session；
3. ``routers/api_auth.py`` 负责从 Cookie 接收 Refresh Token，并把新 Token 写回 Cookie；
4. ``db/redis.py`` 只负责提供 Redis 客户端，本模块才决定 Redis 里保存什么数据。

Refresh Token 是一个无法从内容中读出用户信息的随机字符串。浏览器保存原始字符串，Redis
只保存它的 SHA-256 摘要和最小会话数据。这样既可以在服务端主动撤销登录，又能避免 Redis
泄露后攻击者直接拿存储值冒充浏览器登录。普通 Access JWT 请求不会进入本模块。
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from app.core import get_settings


def _redis_text(value: bytes | str) -> str:
    """把 Redis 返回的文本统一转换成 ``str``。

    项目的 Redis 客户端虽然配置了 ``decode_responses=True``，正常运行时会直接返回
    ``str``，但 redis-py 的类型声明仍需要兼容未开启自动解码的客户端，因此返回类型是
    ``bytes | str``。在 Service 边界集中处理这两种类型，后面的 JSON 解析和 Key 拼接
    就只需要面对明确的 ``str``，也避免在多个调用位置重复使用类型断言。
    """

    return value.decode("utf-8") if isinstance(value, bytes) else value


@dataclass(frozen=True)
class RefreshSessionData:
    """一个 Refresh Session 需要保存在 Redis Value 中的最小数据。

    ``frozen=True`` 让实例创建后不能意外修改字段；轮换会创建新实例，而不是就地改旧实例。
    Redis String 只能保存字符串/字节，因此写入前由 :meth:`to_json` 序列化为 JSON。
    """

    # Refresh Token 本身不携带身份，刷新时需要用这个主键回数据库确认用户仍然存在。
    user_id: int
    # 首次登录时间。轮换时原样继承，用于解释这条会话最初何时建立。
    created_at: str
    # 绝对过期时间。无论刷新多少次都不延长，防止一次登录永久存活。
    expires_at: str
    # 最近一次成功刷新时间，用来计算“连续多久没有使用”的空闲过期。
    last_activity_at: str

    def to_json(self) -> str:
        """转换成稳定 JSON 字符串，供 Redis String Value 保存。"""

        # asdict() 把 dataclass 变成普通字典；紧凑分隔符去掉无意义空格，减少 Redis 占用。
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "RefreshSessionData":
        """从 Redis Value 恢复会话；格式损坏会交给上层按无效会话处理。"""

        # json.loads() 的结果类型比较宽，因此逐字段转换；字段缺失或内容非法会抛出异常，
        # 调用方随后把损坏会话删除并按“未登录”处理，而不是相信不完整数据。
        payload = json.loads(value)
        return cls(
            user_id=int(payload["user_id"]),
            created_at=str(payload["created_at"]),
            expires_at=str(payload["expires_at"]),
            last_activity_at=str(payload["last_activity_at"]),
        )


def _digest(raw_token: str) -> str:
    """把浏览器 Cookie 中的原始 Refresh Token 转换为 SHA-256 十六进制摘要。

    设计目的类似“数据库不保存明文密码”：浏览器发送的 ``raw_token`` 才是真正凭证，
    Redis 只用不可逆摘要定位会话。假设 Redis 内容被读取，攻击者拿到摘要也不能直接把
    它当作 Cookie，因为服务端会再次 SHA-256，得到另一个完全不同的值。

    这里不使用密码专用的 Argon2：Refresh Token 由 ``secrets`` 生成，随机性和长度已经
    足够，不存在人类短密码被字典穷举的问题；SHA-256 更适合高熵 Token 的快速查找。
    ``hexdigest()`` 输出固定 64 个可打印字符，便于安全地组成 Redis Key。
    """

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _session_key(digest: str) -> str:
    """生成单个 Refresh Session 的 Redis Key。

    Key 形状为 ``<项目前缀>:<环境>:auth:refresh:<摘要>``。项目和环境隔离可以避免
    开发、测试、生产共用 Redis 时互相覆盖；摘要用于从 Cookie 快速定位唯一会话。
    """

    settings = get_settings()
    return f"{settings.redis_key_prefix}:{settings.env}:auth:refresh:{digest}"


def _user_sessions_key(user_id: int) -> str:
    """生成某个用户的“会话摘要集合”Key。

    只有 ``摘要 -> 会话`` 还不够：修改密码时我们只知道 user_id，并不知道该用户每台设备
    的原始 Token。额外维护一个 Redis Set，就能先通过 user_id 找到全部摘要，再一次删除
    所有设备会话。Set 天然去重，SADD/SREM/SMEMBERS 正好对应增删查成员。
    """

    settings = get_settings()
    return f"{settings.redis_key_prefix}:{settings.env}:auth:user:{user_id}:refresh-sessions"


def _remaining_seconds(expires_at: datetime, now: datetime) -> int:
    """计算 Redis TTL；向上取整避免还有不足一秒有效期时被提前截断成零。"""

    return max(1, math.ceil((expires_at - now).total_seconds()))


# redis.eval() 会把前 numkeys 个参数放入 KEYS，其余参数放入 ARGV。Redis 官方要求所有
# 会被读写的 Key 都通过 KEYS 传入，便于集群判断脚本操作哪些分片。Lua 脚本执行期间不会
# 被其他 Redis 命令插入，因此下面“写会话 + 更新用户索引”对外表现为一个不可分割的动作。
_CREATE_SCRIPT = """
-- KEYS[1]：单会话 Key；ARGV[1]：会话 JSON；ARGV[2]：剩余有效秒数。
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
-- KEYS[2]：用户会话集合；ARGV[3]：本次 Token 摘要。
redis.call('SADD', KEYS[2], ARGV[3])
-- 集合本身也要过期，否则所有会话到期后会留下没有意义的用户索引。
local current_ttl = redis.call('TTL', KEYS[2])
if current_ttl < tonumber(ARGV[2]) then
    -- 多设备会话期限可能不同，集合至少活到其中当前最长的一条会话结束。
    redis.call('EXPIRE', KEYS[2], ARGV[2])
end
return 1
"""

# Token 轮换不能拆成 Python 中的多条 Redis 命令。否则两个并发刷新请求可能同时读到旧
# Token 有效并各自签发新 Token。Lua 让“检查旧 Key、删除旧 Key、创建新 Key、更新索引”
# 原子执行，从而保证同一个旧 Token 只能成功使用一次。
_ROTATE_SCRIPT = """
-- 第一个刷新请求成功后会删除旧 Key；第二个请求看到不存在，立即失败。
if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
redis.call('DEL', KEYS[1])
redis.call('SREM', KEYS[2], ARGV[1])
-- NX 表示只有新 Key 不存在时才能 SET，极小概率摘要冲突时也不会覆盖已有会话。
local created = redis.call('SET', KEYS[3], ARGV[2], 'EX', ARGV[3], 'NX')
if not created then
    return -1
end
redis.call('SADD', KEYS[2], ARGV[4])
local current_ttl = redis.call('TTL', KEYS[2])
if current_ttl < tonumber(ARGV[3]) then
    redis.call('EXPIRE', KEYS[2], ARGV[3])
end
return 1
"""


async def create_refresh_session(redis: Redis, user_id: int) -> str:
    """登录成功后创建 Redis 会话，返回需要写入 HttpOnly Cookie 的原始 Token。

    调用入口是 ``api_auth._authenticate()``。数据库密码验证成功后才会进入这里。函数把
    摘要和会话数据写入 Redis，却只把原始 Token 返回给 Router；Router 不把它放进 JSON，
    而是写入 JavaScript 无法读取的 HttpOnly Cookie。
    """

    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.refresh_token_expire_minutes)
    # token_urlsafe(48) 使用操作系统安全随机源生成 48 字节随机数据，再编码成适合 Cookie
    # 的 URL-safe 字符串。它不是用户可记忆密码，也不是包含 user_id 的 JWT。
    raw_token = secrets.token_urlsafe(48)
    # 原始 Token 交给浏览器；Redis Key 只使用摘要，服务端也不记录原文。
    digest = _digest(raw_token)
    data = RefreshSessionData(
        user_id=user_id,
        created_at=now.isoformat(),
        expires_at=expires_at.isoformat(),
        last_activity_at=now.isoformat(),
    )
    # Redis TTL 是自动清理机制：到期后 Key 由 Redis 删除，不需要数据库定时清理任务。
    ttl = _remaining_seconds(expires_at, now)
    # Lua 把会话写入和用户索引更新放在一个 Redis 原子操作中，避免只写成功一半。
    await redis.eval(
        _CREATE_SCRIPT,
        2,
        _session_key(digest),
        _user_sessions_key(user_id),
        data.to_json(),
        ttl,
        digest,
    )
    return raw_token


async def rotate_refresh_session(redis: Redis, raw_token: str) -> tuple[int, str] | None:
    """验证旧 Refresh Token，并一次性轮换成新 Token。

    ``POST /api/auth/refresh`` 从 Cookie 得到 ``raw_token`` 后调用本函数。成功返回
    ``(user_id, 新原始 Token)``；不存在、过期、损坏或并发重放都返回 ``None``，Router
    统一转换为 401。每次刷新都换 Token，能缩短旧 Token 被窃取后的可重放窗口。
    """

    # 用与登录时完全相同的算法重算摘要，才能定位当初保存的 Redis Key。
    old_digest = _digest(raw_token)
    old_key = _session_key(old_digest)
    raw_data = await redis.get(old_key)
    # 找不到可能表示过期自动删除、已经退出、已经轮换，或客户端提交了伪造 Token。
    if raw_data is None:
        return None
    try:
        data = RefreshSessionData.from_json(_redis_text(raw_data))
        expires_at = datetime.fromisoformat(data.expires_at).astimezone(UTC)
        last_activity_at = datetime.fromisoformat(data.last_activity_at).astimezone(UTC)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        # Redis Value 被人为改坏时采用失败关闭（fail closed）：删除脏数据并拒绝续签。
        await redis.delete(old_key)
        return None

    now = datetime.now(UTC)
    settings = get_settings()
    idle_timeout = timedelta(minutes=settings.refresh_idle_timeout_minutes)
    # 绝对过期限制总寿命；空闲过期限制长时间未刷新会话。满足任意一个都必须重新登录。
    if now >= expires_at or now - last_activity_at > idle_timeout:
        await revoke_refresh_session(redis, raw_token)
        return None

    new_raw_token = secrets.token_urlsafe(48)
    new_digest = _digest(new_raw_token)
    new_data = RefreshSessionData(
        user_id=data.user_id,
        # 轮换继承首次登录与绝对截止时间，持续刷新不能无限延长会话寿命。
        created_at=data.created_at,
        expires_at=data.expires_at,
        last_activity_at=now.isoformat(),
    )
    ttl = _remaining_seconds(expires_at, now)
    # numkeys=3，所以紧随其后的三个值映射到 Lua KEYS[1..3]；再后面的值依次映射到
    # ARGV[1..4]。参数化传值避免把 Token 数据拼进脚本文本。
    result = await redis.eval(
        _ROTATE_SCRIPT,
        3,
        old_key,
        _user_sessions_key(data.user_id),
        _session_key(new_digest),
        old_digest,
        new_data.to_json(),
        ttl,
        new_digest,
    )
    # 并发刷新时只有第一个请求能删除旧 Key；后续请求必须失败，防止 Token 重放。
    return (data.user_id, new_raw_token) if result == 1 else None


async def revoke_refresh_session(redis: Redis, raw_token: str) -> None:
    """撤销当前设备的 Refresh Session，主要服务于“退出登录”。

    删除操作是幂等的：同一个退出请求执行多次，第二次找不到 Key 也视为成功。这让网络
    重试不会制造额外错误。除了删除单会话 Key，还必须从用户 Set 中移除对应摘要。
    """

    digest = _digest(raw_token)
    key = _session_key(digest)
    raw_data = await redis.get(key)
    if raw_data is None:
        return
    try:
        user_id = RefreshSessionData.from_json(_redis_text(raw_data)).user_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        await redis.delete(key)
        return
    # Pipeline 减少网络往返；transaction=True 使用 MULTI/EXEC，让删除会话和更新索引
    # 一起提交。这里不需要 Lua，因为不存在“先判断再创建”的并发轮换条件。
    async with redis.pipeline(transaction=True) as pipeline:
        pipeline.delete(key)
        pipeline.srem(_user_sessions_key(user_id), digest)
        await pipeline.execute()


async def revoke_user_refresh_sessions(redis: Redis, user_id: int) -> None:
    """撤销一个用户在所有浏览器、手机等设备上的 Refresh Session。

    修改密码、管理员删除用户属于账户安全事件，不能只退出当前浏览器。函数先读取用户
    会话 Set 中的全部摘要，把它们还原成单会话 Key，然后在事务 Pipeline 中删除所有
    会话和索引。它无法立即撤销已经签发的无状态 Access JWT，所以 Access JWT 必须短期。
    """

    index_key = _user_sessions_key(user_id)
    # SMEMBERS 返回这个用户所有设备的 Token 摘要；集合不存在时得到空集合，仍可安全执行。
    digests = await redis.smembers(index_key)
    # smembers() 与 get() 一样被 redis-py 标注为可能返回 bytes；先统一解码，确保
    # _session_key() 接收到的摘要始终是 str，而不是用 cast 掩盖潜在的运行时差异。
    keys = [_session_key(_redis_text(digest)) for digest in digests]
    async with redis.pipeline(transaction=True) as pipeline:
        if keys:
            pipeline.delete(*keys)
        pipeline.delete(index_key)
        await pipeline.execute()
