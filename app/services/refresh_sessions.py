"""使用 Redis 管理可轮换、可撤销的 Refresh Session。

浏览器只保存形如 ``v1.<user_id>.<session_id>.<secret>`` 的 HttpOnly Cookie。Redis
保存 ``secret`` 的 SHA-256 摘要，不保存可直接用于登录的原始凭证。一个设备在整个登录
生命周期中使用稳定的 ``session_id``，轮换只替换密钥摘要，因此退出与轮换始终竞争同一个
Redis Key，不会产生旧 Key 已删除而新 Key 逃过撤销的窗口。

所有属于同一用户的 Key 都包含相同的 Redis Cluster hash tag。用户级 ``generation``
用于密码修改和管理员删除：代次增加后，即使旧会话 Key 尚未被 TTL 清理，也不能再刷新。
本模块不签发 Access JWT，也不读取 Cookie；这些 HTTP 边界仍由 ``routers/api_auth.py`` 负责。

初学者可以按下面四条调用链阅读本文件：

1. 登录成功 -> ``create_refresh_session()`` 创建 Redis 会话；
2. Access Token 过期 -> ``rotate_refresh_session()`` 校验旧密钥并换发新密钥；
3. 用户退出 -> ``revoke_refresh_session()`` 删除当前设备会话；
4. 修改密码或删除账号 -> ``revoke_user_refresh_sessions()`` 一次作废全部设备会话。

这里的 ``user_id`` 和 ``session_id`` 只负责定位 Redis Key，不等于认证成功。真正能证明浏览器
持有有效会话的是随机 ``secret`` 与 Redis 中摘要的匹配结果。
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

TOKEN_VERSION = "v1"


def _redis_text(value: bytes | str) -> str:
    """把 redis-py 可能返回的字节或文本统一转换为字符串。"""

    return value.decode("utf-8") if isinstance(value, bytes) else value


@dataclass(frozen=True)
class RefreshTokenParts:
    """从 Cookie 中解析出的会话定位信息和高熵密钥。

    ``user_id``、``session_id`` 会出现在 Cookie 字符串中，所以不能当作秘密；``secret`` 才是
    需要保密的随机凭证。服务端必须同时检查 Redis 会话和 ``secret`` 摘要，不能只信任前两个
    字段。``frozen=True`` 防止解析后又被意外改写。
    """

    user_id: int
    session_id: str
    secret: str

    @property
    def raw_token(self) -> str:
        """重新组装写回 Cookie 的原始 Token。"""

        return f"{TOKEN_VERSION}.{self.user_id}.{self.session_id}.{self.secret}"


@dataclass(frozen=True)
class RefreshSessionData:
    """单个设备会话保存在 Redis String 中的最小可信状态。

    Redis String 最终只能保存文本，因此调用 :meth:`to_json` 转成 JSON。``token_digest`` 是
    ``secret`` 的 SHA-256 摘要；``generation`` 是用户会话代次；三个时间字段分别记录首次
    登录、绝对截止时间和最近刷新时间。
    """

    user_id: int
    session_id: str
    token_digest: str
    generation: int
    created_at: str
    expires_at: str
    last_activity_at: str

    def to_json(self) -> str:
        """使用紧凑 JSON 序列化，减少 Redis 内存占用。"""

        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> RefreshSessionData:
        """解析 Redis Value；字段缺失或类型错误由调用方按无效会话处理。"""

        payload = json.loads(value)
        return cls(
            user_id=int(payload["user_id"]),
            session_id=str(payload["session_id"]),
            token_digest=str(payload["token_digest"]),
            generation=int(payload["generation"]),
            created_at=str(payload["created_at"]),
            expires_at=str(payload["expires_at"]),
            last_activity_at=str(payload["last_activity_at"]),
        )


def _new_token(user_id: int, session_id: str | None = None) -> RefreshTokenParts:
    """生成稳定会话 ID 和一次性高熵密钥。

    首次登录不传 ``session_id``，因此同时生成会话 ID 和密钥；刷新时传入旧 ``session_id``，
    只更换 ``secret``。这样退出请求即使拿到的是轮换前 Token，也能定位同一条 Redis 会话。
    """

    return RefreshTokenParts(
        user_id=user_id,
        session_id=session_id or secrets.token_urlsafe(24),
        secret=secrets.token_urlsafe(48),
    )


def _parse_token(raw_token: str) -> RefreshTokenParts | None:
    """只解析 Token 外壳，不代表认证成功；格式异常时直接按无效凭证处理。"""

    parts = raw_token.split(".")
    if len(parts) != 4 or parts[0] != TOKEN_VERSION:
        return None
    try:
        user_id = int(parts[1])
    except ValueError:
        return None
    session_id, secret = parts[2], parts[3]
    if user_id <= 0 or not session_id or not secret:
        return None
    return RefreshTokenParts(user_id=user_id, session_id=session_id, secret=secret)


def _digest(secret: str) -> str:
    """计算高熵密钥摘要；Redis 泄露时摘要不能直接作为 Cookie 使用。"""

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _key_base(user_id: int) -> str:
    """生成同一用户共享的 Redis Cluster hash tag 前缀。

    Redis Cluster 使用花括号内的文本计算分片，所以 ``{auth-user-7}`` 能让用户 7 的 Session、
    索引和 generation Key 落到同一分片。Lua 脚本才能原子访问这些 Key，而不会报跨 slot 错误。
    """

    settings = get_settings()
    return f"{settings.redis_key_prefix}:{settings.env}:{{auth-user-{user_id}}}:refresh"


def _session_key(user_id: int, session_id: str) -> str:
    """生成稳定的单设备会话 Key。"""

    return f"{_key_base(user_id)}:session:{session_id}"


def _user_sessions_key(user_id: int) -> str:
    """生成用户当前设备会话 ID 集合 Key。"""

    return f"{_key_base(user_id)}:sessions"


def _generation_key(user_id: int) -> str:
    """生成用户会话撤销代次 Key。"""

    return f"{_key_base(user_id)}:generation"


def _remaining_seconds(expires_at: datetime, now: datetime) -> int:
    """向上取整 TTL，避免不足一秒的有效会话被提前删除。"""

    return max(1, math.ceil((expires_at - now).total_seconds()))


# ``redis.eval(script, numkeys, ...)`` 会把 ``numkeys`` 后面的前几个参数放入 KEYS，其余放入
# ARGV。下面的脚本把“写 Session + 写用户索引”合成一个 Redis 原子操作：执行期间不会插入
# 另一个请求。初学时先对照 create_refresh_session() 中 eval() 的传参顺序阅读：
# KEYS[1]=Session Key，KEYS[2]=用户 Session 集合，KEYS[3]=generation Key；
# ARGV[1]=会话 JSON，ARGV[2]=TTL 秒数，ARGV[3]=session_id，ARGV[4]=读取到的 generation。
_CREATE_SCRIPT = """
local generation = tonumber(redis.call('GET', KEYS[3]) or '0')
if generation ~= tonumber(ARGV[4]) then
    return -1
end
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('SADD', KEYS[2], ARGV[3])
local index_ttl = redis.call('TTL', KEYS[2])
if index_ttl < tonumber(ARGV[2]) then
    redis.call('EXPIRE', KEYS[2], ARGV[2])
end
return 1
"""

# 轮换脚本始终覆盖同一个 Session Key。两个请求同时提交旧 Token 时，第一个会写入新摘要；
# 第二个随后读取到新摘要，与自己的旧摘要不匹配，因此只能有一个请求成功。
# KEYS[1]=Session Key，KEYS[2]=generation Key；ARGV[1]=旧 secret 摘要，
# ARGV[2]=包含新摘要的会话 JSON，ARGV[3]=剩余 TTL。
_ROTATE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then
    return 0
end
local data = cjson.decode(raw)
if data['token_digest'] ~= ARGV[1] then
    return 0
end
local generation = tonumber(redis.call('GET', KEYS[2]) or '0')
if tonumber(data['generation']) ~= generation then
    return -1
end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""

# 单设备撤销同时删除 Session String，并从用户 Set 中移除 session_id，避免留下失效索引。
_REVOKE_SCRIPT = """
redis.call('DEL', KEYS[1])
redis.call('SREM', KEYS[2], ARGV[1])
return 1
"""

# 全量撤销不遍历所有 Session Key，而是把 generation 加一。旧会话仍会按 TTL 自动清理，但
# 因为保存的是旧 generation，下一次刷新会立即失败。这个做法适合 Redis Cluster。
_REVOKE_USER_SCRIPT = """
local generation = redis.call('INCR', KEYS[1])
redis.call('DEL', KEYS[2])
return generation
"""


async def _current_generation(redis: Redis, user_id: int) -> int:
    """读取用户当前会话代次；未发生过全量撤销时为零。"""

    value = await redis.get(_generation_key(user_id))
    return int(_redis_text(value)) if value is not None else 0


async def create_refresh_session(redis: Redis, user_id: int) -> str:
    """登录成功后原子创建设备会话，返回由 Router 写入 HttpOnly Cookie 的内容。

    本函数只返回原始 Token，不把它写日志或 JSON 响应。Redis 保存摘要和会话状态；浏览器
    保存原始 Token。两边缺一都不能刷新 Access Token。
    """

    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.refresh_token_expire_minutes)
    ttl = _remaining_seconds(expires_at, now)

    # 用户代次可能恰好在读取后被密码修改流程提升；Lua 会比较代次，失败时重新读取并重试。
    for _ in range(3):
        token = _new_token(user_id)
        generation = await _current_generation(redis, user_id)
        data = RefreshSessionData(
            user_id=user_id,
            session_id=token.session_id,
            token_digest=_digest(token.secret),
            generation=generation,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            last_activity_at=now.isoformat(),
        )
        result = await redis.eval(
            _CREATE_SCRIPT,
            3,
            _session_key(user_id, token.session_id),
            _user_sessions_key(user_id),
            _generation_key(user_id),
            data.to_json(),
            ttl,
            token.session_id,
            generation,
        )
        # 1 表示创建成功；0 表示极小概率的 session_id 冲突；-1 表示读取 generation 后
        # 恰好发生了改密/删号。后两种情况都重新生成 Token 并读取最新 generation。
        if result == 1:
            return token.raw_token
    raise RuntimeError("Could not create a refresh session after concurrent revocation")


async def rotate_refresh_session(redis: Redis, raw_token: str) -> tuple[int, str] | None:
    """验证并一次性轮换 Refresh Token，同一旧密钥最多成功一次。

    Python 先处理格式、JSON 和时间，便于给异常数据统一失败结果；Lua 再原子完成“比较旧摘要
    -> 检查 generation -> 覆盖新摘要”，解决并发请求可能同时通过 Python 预检查的问题。
    成功返回 ``(user_id, 新 Token)``，任何无效、过期或重放情况都返回 ``None``。
    """

    token = _parse_token(raw_token)
    if token is None:
        return None
    key = _session_key(token.user_id, token.session_id)
    raw_data = await redis.get(key)
    if raw_data is None:
        return None
    try:
        data = RefreshSessionData.from_json(_redis_text(raw_data))
        expires_at = datetime.fromisoformat(data.expires_at).astimezone(UTC)
        last_activity_at = datetime.fromisoformat(data.last_activity_at).astimezone(UTC)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        await revoke_refresh_session(redis, raw_token)
        return None
    if data.user_id != token.user_id or data.session_id != token.session_id:
        await revoke_refresh_session(redis, raw_token)
        return None

    now = datetime.now(UTC)
    settings = get_settings()
    if now >= expires_at or now - last_activity_at > timedelta(
        minutes=settings.refresh_idle_timeout_minutes
    ):
        await revoke_refresh_session(redis, raw_token)
        return None

    new_token = _new_token(token.user_id, token.session_id)
    new_data = RefreshSessionData(
        user_id=data.user_id,
        session_id=data.session_id,
        token_digest=_digest(new_token.secret),
        generation=data.generation,
        created_at=data.created_at,
        expires_at=data.expires_at,
        last_activity_at=now.isoformat(),
    )
    result = await redis.eval(
        _ROTATE_SCRIPT,
        2,
        key,
        _generation_key(token.user_id),
        _digest(token.secret),
        new_data.to_json(),
        _remaining_seconds(expires_at, now),
    )
    return (token.user_id, new_token.raw_token) if result == 1 else None


async def revoke_refresh_session(redis: Redis, raw_token: str) -> None:
    """原子撤销当前设备会话；Token 已过期或重复退出也视为成功。

    撤销只使用公开的 user_id/session_id 定位稳定 Key，不要求 secret 仍是最新版。因此用户
    点击退出时，即使请求携带的是刚轮换前的 Token，也能删除当前设备已经轮换后的会话。
    """

    token = _parse_token(raw_token)
    if token is None:
        return
    await redis.eval(
        _REVOKE_SCRIPT,
        2,
        _session_key(token.user_id, token.session_id),
        _user_sessions_key(token.user_id),
        token.session_id,
    )


async def revoke_user_refresh_sessions(redis: Redis, user_id: int) -> None:
    """提升用户会话代次，使所有既有设备会话立即且不可逆地失效。

    可以把 generation 理解成“用户所有会话的版本号”：登录时会话记住当前版本，改密或删号
    时版本加一，旧版本会话就不能继续刷新。旧 Session String 最迟会在绝对有效期到达时由
    TTL 清理。这里不遍历动态 Key，避免 Redis Cluster Lua 脚本访问不同分片；删除设备索引
    后，新登录会从当前版本建立干净索引。
    """

    await redis.eval(
        _REVOKE_USER_SCRIPT,
        2,
        _generation_key(user_id),
        _user_sessions_key(user_id),
    )
