"""通过 SMTP 发送业务邮件。

本模块把同步的 ``smtplib`` 调用放到 AnyIO 工作线程，避免阻塞 FastAPI 事件循环。
它只负责邮件传输，不负责生成验证码、查询用户或修改密码；SMTP 凭据也不会写入日志。
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from functools import partial
from html import escape

from anyio import to_thread

from app.core import get_settings


class EmailDeliveryError(RuntimeError):
    """SMTP 未配置或邮件投递失败。"""


def _send_sync(
    *,
    host: str,
    port: int,
    use_ssl: bool,
    username: str,
    password: str,
    from_email: str,
    recipient: str,
    code: str,
    timeout: float,
    from_name: str,
    title: str,
) -> None:
    """在工作线程中建立 SMTP 连接并发送邮件。"""

    message = EmailMessage()
    message["Subject"] = f"【{title}】密码重置验证码"
    message["From"] = formataddr((from_name, from_email))
    message["To"] = recipient
    message.set_content(
        "您好！\n\n"
        f"我们收到了重置您 {title} 账号密码的请求。\n\n"
        f"您的密码重置验证码：{code}\n\n"
        "验证码有效期为 10 分钟，且只能使用一次。\n"
        "如果这不是您本人发起的操作，请忽略此邮件，并不要向任何人透露验证码。\n\n"
        "此邮件由系统自动发送，请勿直接回复。\n"
    )
    safe_code = escape(code)
    message.add_alternative(
        f"""\
<!doctype html>
<html lang="zh-CN">
  <body style="margin:0;background:#f4f7fb;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;">
    <div style="max-width:560px;margin:32px auto;padding:32px 28px;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;">
      <h2 style="margin:0 0 20px;color:#111827;font-size:22px;">{title} 密码重置</h2>
      <p style="margin:0 0 14px;line-height:1.7;">您好！</p>
      <p style="margin:0 0 20px;line-height:1.7;">我们收到了重置您 {title} 账号密码的请求。</p>
      <div style="margin:0 0 20px;padding:18px;text-align:center;background:#eff6ff;border-radius:8px;">
        <div style="margin-bottom:8px;color:#4b5563;font-size:14px;">您的密码重置验证码</div>
        <div style="color:#1d4ed8;font-size:32px;font-weight:700;letter-spacing:8px;">{safe_code}</div>
      </div>
      <p style="margin:0 0 10px;line-height:1.7;"><strong>有效期：</strong>10 分钟</p>
      <p style="margin:0 0 18px;line-height:1.7;">验证码只能使用一次。若不是您本人发起的操作，请忽略此邮件，并不要向任何人透露验证码。</p>
      <hr style="border:0;border-top:1px solid #e5e7eb;margin:22px 0;">
      <p style="margin:0;color:#6b7280;font-size:13px;line-height:1.6;">此邮件由系统自动发送，请勿直接回复。</p>
    </div>
  </body>
</html>
""",
        subtype="html",
    )

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
                server.login(username, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(username, password)
                server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        # 对外只返回统一的投递失败；主机、用户名和授权码不得进入 API 响应或日志。
        raise EmailDeliveryError("Unable to deliver password reset email") from exc


async def send_password_reset_email(recipient: str, code: str) -> None:
    """使用当前 Settings 向 ``recipient`` 发送密码重置验证码。"""

    settings = get_settings()
    username = (settings.smtp_username or "").strip()
    password = settings.smtp_password.get_secret_value() if settings.smtp_password else ""
    from_email = (settings.smtp_from_email or username).strip()
    if not username or not password or not from_email:
        raise EmailDeliveryError("SMTP is not configured")

    send = partial(
        _send_sync,
        host=settings.smtp_host,
        port=settings.smtp_port,
        use_ssl=settings.smtp_use_ssl,
        username=username,
        password=password,
        from_email=from_email,
        from_name=settings.smtp_from_name,
        recipient=recipient,
        code=code,
        timeout=settings.smtp_timeout_seconds,
        title=settings.project_title,
    )
    await to_thread.run_sync(send)
