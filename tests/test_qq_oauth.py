"""QQ 互联登录的 state、第三方响应解析、账号创建与回调测试。"""

import pytest
from fakeredis.aioredis import FakeRedis
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.services import password_reset, qq_oauth
from app.templating import templates

pytestmark = pytest.mark.anyio


@pytest.fixture
def configured_qq(monkeypatch: pytest.MonkeyPatch) -> None:
    """让路由测试进入 QQ 已配置分支，不读取或输出真实应用密钥。"""

    monkeypatch.setattr(qq_oauth, "is_configured", lambda: True)


async def test_state_is_single_use_and_next_path_is_restricted(
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """state 只能消费一次，外站 URL 不能成为 OAuth 完成后的跳转目标。"""

    monkeypatch.setattr(qq_oauth, "is_configured", lambda: True)
    state, next_path = await qq_oauth.create_state(fake_redis, "https://evil.example/path")

    assert next_path == "/"
    assert await qq_oauth.consume_state(fake_redis, state) == "/"
    with pytest.raises(qq_oauth.QQOAuthStateError):
        await qq_oauth.consume_state(fake_redis, state)


async def test_qq_profile_response_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """兼容 QQ 经典 query-string Token 与 JSONP openid 响应。"""

    class StubResponse:
        def __init__(self, text: str = "", payload: dict | None = None) -> None:
            self.text = text
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            assert self._payload is not None
            return self._payload

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str, params: dict[str, str]):
            if url.endswith("/token"):
                return StubResponse("access_token=qq-access-token&expires_in=3600")
            if url.endswith("/me"):
                return StubResponse('callback( {"client_id":"10001","openid":"qq-open-id"} );')
            return StubResponse(payload={"ret": 0, "nickname": " QQ 测试用户 "})

    monkeypatch.setattr(qq_oauth.httpx, "AsyncClient", lambda **_kwargs: StubClient())
    monkeypatch.setattr(qq_oauth, "is_configured", lambda: True)
    settings = qq_oauth.get_settings()
    monkeypatch.setattr(settings, "qq_client_id", "10001")
    monkeypatch.setattr(settings, "qq_client_secret", type(settings.secret_key)("secret"))
    monkeypatch.setattr(settings, "qq_redirect_uri", "http://test/api/auth/qq/callback")

    token = await qq_oauth.exchange_code("authorization-code")
    profile = await qq_oauth.fetch_profile(token)

    assert token == "qq-access-token"
    assert profile == qq_oauth.QQProfile(openid="qq-open-id", nickname="QQ 测试用户")


async def test_first_qq_login_creates_one_reusable_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """相同 openid 重复登录复用同一用户，QQ-only 账号不伪造本地密码。"""

    profile = qq_oauth.QQProfile(openid="stable-openid", nickname="QQ 昵称")
    async with session_factory() as session:
        first = await qq_oauth.get_or_create_user(session, profile)
        second = await qq_oauth.get_or_create_user(session, profile)
        count = await session.scalar(select(func.count()).select_from(User))

    assert first.id == second.id
    assert count == 1
    assert first.provider == "qq"
    assert first.provider_user_id == "stable-openid"
    assert first.nickname == "QQ 昵称"
    assert first.email.endswith("@qq-accounts.internal")
    assert first.hashed_password is None
    assert first.has_password is False

    async with session_factory() as session:
        normalized = await qq_oauth.user_service.get_user_by_provider_identity(
            session, " QQ ", " stable-openid "
        )

    assert normalized is not None
    assert normalized.id == first.id


async def test_provider_identity_requires_both_values(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """第三方身份不能只提供平台名或只提供平台用户 ID。"""

    async with session_factory() as session:
        with pytest.raises(ValueError, match="provided together"):
            await qq_oauth.user_service.get_user_by_provider_identity(session, "qq", " ")


async def test_provider_namespace_allows_same_id_on_different_platforms(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """provider 是命名空间，微信相同文本 ID 不能冒充 QQ 身份。"""

    async with session_factory() as session:
        session.add(
            User(
                username="wechat_user",
                email="wechat@example.com",
                hashed_password=None,
                nickname="微信用户",
                provider="wechat",
                provider_user_id="same-id",
            )
        )
        await session.commit()
        qq_user = await qq_oauth.get_or_create_user(
            session,
            qq_oauth.QQProfile(openid="same-id", nickname="QQ 用户"),
        )
        count = await session.scalar(select(func.count()).select_from(User))

    assert qq_user.provider == "qq"
    assert qq_user.provider_user_id == "same-id"
    assert count == 2


async def test_internal_qq_email_does_not_send_password_reset_mail(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """占位邮箱不可投递；接口保持枚举防护，但不会连接 SMTP。"""

    sent: list[str] = []

    async def fake_send(email: str, _code: str) -> None:
        sent.append(email)

    monkeypatch.setattr("app.services.email.send_password_reset_email", fake_send)
    async with session_factory() as session:
        session.add(
            User(
                username="qq_user",
                email="qq_user@qq-accounts.internal",
                hashed_password="hash",
                nickname="QQ 用户",
                provider="qq",
                provider_user_id="openid",
            )
        )
        await session.commit()
        await password_reset.request_password_reset(
            session,
            fake_redis,
            "qq_user@qq-accounts.internal",
        )

    assert sent == []
    assert not [key async for key in fake_redis.scan_iter("*:password-reset:code:*")]


async def test_qq_login_redirects_to_authorization_page(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    configured_qq: None,
) -> None:
    """登录入口先保存 state，再使用 302 跳转 QQ 授权页。"""

    async def fake_create_state(_redis, next_path):
        assert next_path == "/posts"
        return "one-time-state", "/posts"

    monkeypatch.setattr(qq_oauth, "create_state", fake_create_state)
    monkeypatch.setattr(
        qq_oauth,
        "authorization_url",
        lambda state: f"https://graph.qq.com/oauth2.0/authorize?state={state}",
    )

    response = await client.get("/api/auth/qq/login", params={"next_path": "/posts"})

    assert response.status_code == 302
    assert response.headers["location"].endswith("state=one-time-state")


async def test_qq_login_returns_503_when_not_configured(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置 QQ 应用时普通密码登录可用，但直接访问 QQ 入口得到明确 503。"""

    monkeypatch.setattr(qq_oauth, "is_configured", lambda: False)

    response = await client.get("/api/auth/qq/login")

    assert response.status_code == 503
    assert response.json()["message"] == "QQ login is not configured"


async def test_qq_callback_creates_blog_session_without_token_in_url(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QQ 回调只写 HttpOnly Refresh Cookie，本站 Access Token 再由 refresh 接口取得。"""

    async with session_factory() as session:
        user = User(
            username="qq_user",
            email="qq_user@qq-accounts.internal",
            hashed_password="hash",
            nickname="QQ 用户",
            provider="qq",
            provider_user_id="openid",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    async def fake_consume_state(_redis, state: str) -> str:
        assert state == "valid-state"
        return "/posts"

    async def fake_exchange_code(code: str) -> str:
        assert code == "valid-code"
        return "third-party-token"

    async def fake_fetch_profile(token: str) -> qq_oauth.QQProfile:
        assert token == "third-party-token"
        return qq_oauth.QQProfile(openid="openid", nickname="QQ 用户")

    async def fake_get_or_create_user(session: AsyncSession, _profile: qq_oauth.QQProfile) -> User:
        stored = await session.get(User, user_id)
        assert stored is not None
        return stored

    monkeypatch.setattr(qq_oauth, "consume_state", fake_consume_state)
    monkeypatch.setattr(qq_oauth, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(qq_oauth, "fetch_profile", fake_fetch_profile)
    monkeypatch.setattr(qq_oauth, "get_or_create_user", fake_get_or_create_user)

    callback = await client.get(
        "/api/auth/qq/callback",
        params={"code": "valid-code", "state": "valid-state"},
    )
    refresh = await client.post("/api/auth/refresh")

    assert callback.status_code == 303
    assert callback.headers["location"] == "/login?qq=success&next=%2Fposts"
    assert "third-party-token" not in callback.headers["location"]
    assert callback.cookies.get("refresh_token")
    assert refresh.status_code == 200
    assert refresh.json()["data"]["access_token"]


async def test_qq_callback_rejects_missing_or_replayed_state(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少参数或 state 失效时回到登录页，不签发 Refresh Session。"""

    async def rejected_state(_redis, _state: str) -> str:
        raise qq_oauth.QQOAuthStateError

    monkeypatch.setattr(qq_oauth, "consume_state", rejected_state)

    missing = await client.get("/api/auth/qq/callback", params={"error": "access_denied"})
    replayed = await client.get(
        "/api/auth/qq/callback",
        params={"code": "code", "state": "used-state"},
    )

    assert missing.status_code == replayed.status_code == 303
    assert missing.headers["location"] == replayed.headers["location"] == "/login?qq=error&next=%2F"
    assert missing.cookies.get("refresh_token") is None
    assert replayed.cookies.get("refresh_token") is None


async def test_qq_buttons_only_render_when_configuration_is_complete(client: AsyncClient) -> None:
    """模板只接收布尔开关，不会把 QQ Client Secret 输出到 HTML。"""

    original = templates.env.globals["qq_login_enabled"]
    try:
        templates.env.globals["qq_login_enabled"] = True
        login_page = await client.get("/login")
        register_page = await client.get("/register")
        admin_page = await client.get("/admin")
    finally:
        templates.env.globals["qq_login_enabled"] = original

    assert "使用 QQ 登录" in login_page.text
    assert "使用 QQ 登录并注册" in register_page.text
    assert "data-qq-login" in admin_page.text
    assert "QQ_CLIENT_SECRET" not in login_page.text
