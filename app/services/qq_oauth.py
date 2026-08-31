"""QQ 互联 OAuth 2.0 的外部调用与登录状态服务。

本模块只负责 QQ 授权码交换、openid/昵称获取和 Redis ``state`` 防重放；它不会读取 HTTP
请求、设置 Cookie 或直接返回状态码。Router 负责浏览器重定向，用户 Service 负责数据库
账号创建，认证 Router 负责把最终用户转换为本项目自己的 JWT/Refresh Session。
"""

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_settings
from app.models import User
from app.services import users as user_service

# QQ 只是通用 provider 字段的一个取值。QQ API 使用 ``openid`` 命名身份 ID，
# 本项目落库时统一映射为 ``provider_user_id``，因此后续微信等平台可以复用同一套
# 账号绑定 Service，而不需要再增加 qq_openid、wechat_openid 等相互独立的字段。
QQ_PROVIDER = "qq"


class QQOAuthError(Exception):
    """QQ 授权服务不可用、返回格式错误或用户拒绝授权。"""


class QQOAuthStateError(Exception):
    """回调 state 缺失、过期或已被消费。"""


@dataclass(frozen=True)
class QQProfile:
    """从 QQ 互联接口取得的最小公开资料。

    ``openid`` 保留 QQ API 的领域命名，便于对应官方响应；真正写入 users 表时，
    ``get_or_create_user`` 会把它映射成通用的 ``provider_user_id``。昵称只用于展示，
    因为用户可以在 QQ 侧修改昵称，所以不能把昵称当作账号主键。
    """

    openid: str
    nickname: str


def is_configured() -> bool:
    """判断 QQ 登录所需三项配置是否齐全；未配置时不暴露登录入口。"""

    settings = get_settings()
    return bool(settings.qq_client_id and settings.qq_client_secret and settings.qq_redirect_uri)


def _state_key(state: str) -> str:
    """生成 Redis state Key；state 原文只作为随机键，不写入日志。"""

    settings = get_settings()
    return f"{settings.redis_key_prefix}:{settings.env}:qq:oauth:state:{state}"


def _validate_next(next_path: str | None) -> str:
    """只允许站内绝对路径，防止 QQ 回调被利用为开放重定向。"""

    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/"
    if any(
        next_path == prefix or next_path.startswith(f"{prefix}/")
        for prefix in ("/api", "/static", "/media")
    ):
        return "/"
    if next_path == "/login":
        return "/"
    return next_path


async def create_state(redis: Redis, next_path: str | None = None) -> tuple[str, str]:
    """创建一次性 OAuth state，并在 Redis 中保存登录成功后的站内跳转路径。"""

    # state 是随机且短时有效的回调关联凭证。Redis 只保存经过校验的站内返回路径，
    # 不把密码、access_token 或其他敏感资料塞进 state，避免浏览器回调泄露内部数据。
    if not is_configured():
        raise QQOAuthError("QQ login is not configured")
    state = secrets.token_urlsafe(32)
    await redis.set(
        _state_key(state),
        _validate_next(next_path),
        ex=get_settings().qq_oauth_state_ttl_seconds,
        nx=True,
    )
    return state, _validate_next(next_path)


async def consume_state(redis: Redis, state: str) -> str:
    """原子地读取并删除 state；重复回调或过期回调都会失败。"""

    if not state or len(state) > 128:
        raise QQOAuthStateError
    key = _state_key(state)
    # GETDEL 保证同一个 state 只有第一个回调可以成功，阻止浏览器刷新或攻击者重放。
    next_path = await redis.getdel(key)
    if next_path is None:
        raise QQOAuthStateError
    if isinstance(next_path, bytes):
        next_path = next_path.decode("utf-8")
    return _validate_next(next_path)


def authorization_url(state: str) -> str:
    """生成 QQ 授权页 URL；client_secret 永远不进入浏览器地址。"""

    from urllib.parse import urlencode

    settings = get_settings()
    if not is_configured():
        raise QQOAuthError("QQ login is not configured")
    return f"{settings.qq_authorize_url}?{
        urlencode(
            {
                'response_type': 'code',
                'client_id': settings.qq_client_id,
                'redirect_uri': settings.qq_redirect_uri,
                'state': state,
            }
        )
    }"


def _json_object(value: Any) -> dict[str, Any]:
    """确保第三方响应是 JSON 对象，避免把异常字符串当作用户资料。"""

    if not isinstance(value, dict):
        raise QQOAuthError("QQ response is not an object")
    return value


async def _get(client: httpx.AsyncClient, url: str, params: dict[str, str]) -> httpx.Response:
    """执行带超时的 QQ 请求并统一转换网络异常。"""

    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
    except (httpx.HTTPError, OSError):
        # HTTPX 异常可能在 repr 中包含带 access_token 的完整 URL；不要把原异常链交给
        # 全局日志处理器，避免第三方令牌进入日志。
        raise QQOAuthError("QQ service request failed") from None
    return response


async def exchange_code(code: str) -> str:
    """用一次性授权码换取 QQ access_token；不把令牌写入日志或数据库。"""

    # authorization code 只能使用一次。client_secret 只在服务端换 token 时发送，
    # 不能进入日志或错误消息，否则泄露后他人可以伪造本应用请求 QQ。
    settings = get_settings()
    if not code or len(code) > 512 or not is_configured():
        raise QQOAuthError("Invalid QQ authorization code")
    try:
        async with httpx.AsyncClient(timeout=settings.qq_http_timeout_seconds) as client:
            response = await client.get(
                settings.qq_token_url,
                params={
                    "grant_type": "authorization_code",
                    "client_id": settings.qq_client_id or "",
                    "client_secret": settings.qq_client_secret.get_secret_value()
                    if settings.qq_client_secret
                    else "",
                    "code": code,
                    "redirect_uri": settings.qq_redirect_uri or "",
                },
            )
            response.raise_for_status()
    except (httpx.HTTPError, OSError):
        # Token 请求 URL 含 client_secret，必须抑制原异常链，避免日志泄露应用密钥。
        raise QQOAuthError("QQ token request failed") from None
    values = parse_qs(response.text, keep_blank_values=False)
    token = values.get("access_token", [None])[0]
    if not token:
        raise QQOAuthError("QQ token response is invalid")
    return token


def _parse_openid(text: str) -> str:
    """解析 QQ ``callback({...});`` 格式的 openid 响应。"""

    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise QQOAuthError("QQ openid response is invalid")
    try:
        payload = _json_object(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, QQOAuthError) as exc:
        raise QQOAuthError("QQ openid response is invalid") from exc
    openid = payload.get("openid")
    if not isinstance(openid, str) or not openid or len(openid) > 128:
        raise QQOAuthError("QQ openid is invalid")
    return openid


async def fetch_profile(access_token: str) -> QQProfile:
    """使用 access_token 读取 openid 和昵称，昵称仅作为首次注册时的展示名。"""

    # access_token 只在本次服务端请求链中短暂存在：用它取得稳定身份和展示昵称后立即
    # 丢弃。昵称可变，不能作为账号 ID；token 也不写入数据库、Redis、Cookie 或日志。
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.qq_http_timeout_seconds) as client:
        openid_response = await _get(
            client,
            settings.qq_openid_url,
            {"access_token": access_token},
        )
        openid = _parse_openid(openid_response.text)
        user_response = await _get(
            client,
            settings.qq_userinfo_url,
            {
                "access_token": access_token,
                "oauth_consumer_key": settings.qq_client_id or "",
                "openid": openid,
            },
        )
    try:
        payload = _json_object(user_response.json())
    except (ValueError, QQOAuthError) as exc:
        raise QQOAuthError("QQ user response is invalid") from exc
    if payload.get("ret", 0) != 0:
        raise QQOAuthError("QQ user response rejected")
    nickname = payload.get("nickname")
    if not isinstance(nickname, str) or not nickname.strip():
        nickname = "QQ 用户"
    return QQProfile(openid=openid, nickname=nickname.strip()[:50])


async def get_or_create_user(session: AsyncSession, profile: QQProfile) -> User:
    """按第三方复合身份查找用户，不存在时创建一个没有本站密码的 QQ 账号。

    这里的“复合身份”是 ``(provider, provider_user_id)``：QQ 的 provider 固定为
    ``qq``，QQ API 返回的 openid 作为 provider_user_id。不能只按 openid 查找，
    因为未来微信可能返回相同文本的 ID；也不能按邮箱或昵称自动合并已有密码账号，
    因为它们可能是占位值或可修改资料，自动合并会产生账号接管风险。
    """

    # openid 只在 QQ 平台命名空间内稳定。查询时必须同时带平台名和平台 ID；只查 ID
    # 可能与未来微信的相同文本值碰撞。
    # openid 只是 QQ API 的字段名；落库时统一映射为 provider_user_id。
    # provider 与 provider_user_id 必须一起查询，避免 QQ 和未来微信的 ID 发生碰撞。
    # 不按昵称或邮箱自动合并已有账号，因为它们可能为空、可修改或只是占位值。
    existing = await user_service.get_user_by_provider_identity(
        session, QQ_PROVIDER, profile.openid
    )
    if existing is not None:
        return existing
    # QQ 接口不会返回可用于密码找回的邮箱，因此为 OAuth 账号生成内部占位邮箱。
    # 用户首次登录后可以在个人资料中绑定真实邮箱；随机密码只用于满足现有数据模型，
    # 不会展示或记录，QQ 账号应通过 QQ 登录进入系统。
    digest = hashlib.sha256(profile.openid.encode("utf-8")).hexdigest()[:32]
    try:
        return await user_service.create_provider_user(
            session,
            provider=QQ_PROVIDER,
            provider_user_id=profile.openid,
            username=f"qq_{digest[:40]}",
            email=f"qq_{digest}@qq-accounts.internal",
            nickname=profile.nickname,
        )
    except user_service.UserAlreadyExistsError as exc:
        raise QQOAuthError("QQ account could not be created") from exc
