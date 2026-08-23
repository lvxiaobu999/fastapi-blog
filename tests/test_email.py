"""密码重置邮件主题、纯文本正文和 HTML 正文测试。"""

from email.message import EmailMessage
from typing import Self

import pytest

from app.services import email as email_service


def test_password_reset_email_contains_readable_plain_and_html_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """邮件客户端不支持 HTML 时仍能看到验证码和完整安全提示。"""

    sent: list[EmailMessage] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            assert host == "smtp.qq.com"
            assert port == 465
            assert timeout == 10

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            assert username == "sender@qq.com"
            assert password == "test-authorization-code"

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", FakeSMTP)
    email_service._send_sync(
        host="smtp.qq.com",
        port=465,
        use_ssl=True,
        username="sender@qq.com",
        password="test-authorization-code",
        from_email="sender@qq.com",
        from_name="三碗博客",
        recipient="user@example.com",
        code="012345",
        timeout=10,
        title="三碗博客",
    )

    assert len(sent) == 1
    message = sent[0]
    assert message["Subject"] == "【三碗博客】密码重置验证码"
    assert message["From"] == "三碗博客 <sender@qq.com>"
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "012345" in plain
    assert "有效期为 10 分钟" in plain
    assert "不要向任何人透露验证码" in plain
    assert "012345" in html
    assert "三碗博客 密码重置" in html
    assert "此邮件由系统自动发送，请勿直接回复" in html
