"""忘记密码邮箱验证码流程的 API 与会话撤销测试。"""

import pytest
from httpx import AsyncClient

from app.services.email import EmailDeliveryError

pytestmark = pytest.mark.anyio


async def register(client: AsyncClient, username: str = "reset-user") -> dict:
    """创建带已知密码和邮箱的测试用户。"""

    response = await client.post(
        "/api/users",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201
    return response.json()["data"]


async def test_request_code_hides_account_existence_and_enforces_cooldown(
    client: AsyncClient,
    fake_redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已注册和未注册邮箱返回相同消息，重复请求不会重复发信。"""

    await register(client)
    sent_codes: list[str] = []

    async def fake_send(recipient: str, code: str) -> None:
        sent_codes.append(code)

    monkeypatch.setattr("app.services.email.send_password_reset_email", fake_send)
    existing = await client.post(
        "/api/auth/password-reset/request", json={"email": " RESET-USER@EXAMPLE.COM "}
    )
    missing = await client.post(
        "/api/auth/password-reset/request", json={"email": "missing@example.com"}
    )
    repeated = await client.post(
        "/api/auth/password-reset/request", json={"email": "reset-user@example.com"}
    )

    assert existing.status_code == missing.status_code == repeated.status_code == 200
    assert existing.json()["message"] == missing.json()["message"]
    assert len(sent_codes) == 1
    assert len(sent_codes[0]) == 6 and sent_codes[0].isdigit()
    stored = [key async for key in fake_redis.scan_iter("*:password-reset:code:*")]
    assert len(stored) == 1
    assert sent_codes[0] not in (await fake_redis.get(stored[0]))


async def test_smtp_failure_returns_503_without_exposing_credentials(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """邮件服务异常映射为 503，响应不包含 SMTP 细节。"""

    await register(client, username="smtp-user")

    async def failing_send(recipient: str, code: str) -> None:
        raise EmailDeliveryError("internal SMTP details")

    monkeypatch.setattr("app.services.email.send_password_reset_email", failing_send)
    response = await client.post(
        "/api/auth/password-reset/request", json={"email": "smtp-user@example.com"}
    )

    assert response.status_code == 503
    assert response.json()["message"] == "Email service temporarily unavailable"
    assert "SMTP" not in response.text


async def test_confirm_code_changes_password_and_revokes_refresh_sessions(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正确验证码只能使用一次，新密码生效且旧 Refresh Token 失效。"""

    await register(client, username="confirm-user")
    login_response = await client.post(
        "/api/auth/token", data={"username": "confirm-user", "password": "password123"}
    )
    old_refresh = login_response.cookies.get("refresh_token")
    sent_codes: list[str] = []

    async def fake_send(recipient: str, code: str) -> None:
        sent_codes.append(code)

    monkeypatch.setattr("app.services.email.send_password_reset_email", fake_send)
    requested = await client.post(
        "/api/auth/password-reset/request", json={"email": "confirm-user@example.com"}
    )
    assert requested.status_code == 200

    reset = await client.post(
        "/api/auth/password-reset/confirm",
        json={
            "email": " CONFIRM-USER@EXAMPLE.COM ",
            "code": sent_codes[0],
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        },
    )
    replay = await client.post(
        "/api/auth/password-reset/confirm",
        json={
            "email": "confirm-user@example.com",
            "code": sent_codes[0],
            "new_password": "anotherpassword123",
            "confirm_password": "anotherpassword123",
        },
    )

    assert reset.status_code == 200
    assert replay.status_code == 400
    assert (
        await client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={old_refresh}"})
    ).status_code == 401
    assert (
        await client.post(
            "/api/auth/token", data={"username": "confirm-user", "password": "password123"}
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/auth/token",
            data={"username": "confirm-user", "password": "newpassword123"},
        )
    ).status_code == 200


async def test_wrong_code_is_limited_and_expiry_is_reported_as_bad_request(
    client: AsyncClient,
    fake_redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """错误验证码累计到上限后删除记录，之后统一返回 400。"""

    await register(client, username="attempt-user")
    sent_codes: list[str] = []

    async def fake_send(recipient: str, code: str) -> None:
        sent_codes.append(code)

    monkeypatch.setattr("app.services.email.send_password_reset_email", fake_send)
    await client.post(
        "/api/auth/password-reset/request", json={"email": "attempt-user@example.com"}
    )
    payload = {
        "email": "attempt-user@example.com",
        "code": "000000" if sent_codes[0] != "000000" else "111111",
        "new_password": "newpassword123",
        "confirm_password": "newpassword123",
    }
    for _ in range(5):
        response = await client.post("/api/auth/password-reset/confirm", json=payload)
        assert response.status_code == 400
    keys = [key async for key in fake_redis.scan_iter("*:password-reset:code:*")]
    assert keys == []


async def test_confirm_rejects_malformed_code_before_service(client: AsyncClient) -> None:
    """验证码必须是六位数字，Pydantic 在 Router 前返回 422。"""

    response = await client.post(
        "/api/auth/password-reset/confirm",
        json={
            "email": "user@example.com",
            "code": "abc",
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        },
    )

    assert response.status_code == 422
