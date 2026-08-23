"""应用配置组合校验测试；不连接数据库、Redis 或其他外部服务。"""

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


def production_settings(
    *,
    auth_cookie_secure: bool,
    public_ip_mode: bool = False,
    allowed_hosts: list[str] | None = None,
) -> Settings:
    """构造最小生产配置，供安全组合测试复用。"""

    return Settings(
        env="production",
        database_url="postgresql+psycopg://app:placeholder@db.internal/blog",
        secret_key=SecretStr("test-only-secret-with-at-least-32-characters"),
        redis_url=SecretStr("rediss://cache.internal:6379/0"),
        allowed_hosts=allowed_hosts or ["blog.example.com"],
        auth_cookie_secure=auth_cookie_secure,
        public_ip_mode=public_ip_mode,
        _env_file=None,
    )


def test_production_rejects_insecure_refresh_cookie() -> None:
    """生产环境不能因漏配而把 Refresh Cookie 降级为非 Secure。"""

    with pytest.raises(ValidationError, match="AUTH_COOKIE_SECURE must be true"):
        production_settings(auth_cookie_secure=False)


def test_production_accepts_secure_refresh_cookie() -> None:
    """完整的生产安全组合应通过 Settings 启动校验。"""

    settings = production_settings(auth_cookie_secure=True)

    assert settings.env == "production"
    assert settings.auth_cookie_secure is True


def test_public_ip_mode_accepts_http_cookie_for_ipv4_host() -> None:
    """临时公网 IP 模式允许 HTTP Cookie，但只接受明确的 IPv4 Host。"""

    settings = production_settings(
        auth_cookie_secure=False,
        public_ip_mode=True,
        allowed_hosts=["203.0.113.10"],
    )

    assert settings.public_ip_mode is True
    assert settings.allowed_hosts == ["203.0.113.10"]


def test_public_ip_mode_rejects_domain_or_secure_cookie() -> None:
    """公网 IP 开关不能被复用于正式域名，也不能与 Secure Cookie 矛盾。"""

    with pytest.raises(ValidationError, match="IPv4 addresses only"):
        production_settings(
            auth_cookie_secure=False,
            public_ip_mode=True,
            allowed_hosts=["blog.example.com"],
        )

    with pytest.raises(ValidationError, match="AUTH_COOKIE_SECURE=false"):
        production_settings(
            auth_cookie_secure=True,
            public_ip_mode=True,
            allowed_hosts=["203.0.113.10"],
        )


def test_production_rejects_short_secret_and_wildcard_host() -> None:
    """生产密钥必须足够长，Host 白名单不能退化为接受任意来源。"""

    with pytest.raises(ValidationError, match="SECRET_KEY must contain at least 32"):
        Settings(
            env="production",
            database_url="postgresql+psycopg://app:placeholder@db.internal/blog",
            secret_key=SecretStr("too-short"),
            redis_url=SecretStr("rediss://cache.internal:6379/0"),
            auth_cookie_secure=True,
            allowed_hosts=["blog.example.com"],
            _env_file=None,
        )

    with pytest.raises(ValidationError, match="explicit hostnames"):
        Settings(
            env="production",
            database_url="postgresql+psycopg://app:placeholder@db.internal/blog",
            secret_key=SecretStr("test-only-secret-with-at-least-32-characters"),
            redis_url=SecretStr("rediss://cache.internal:6379/0"),
            auth_cookie_secure=True,
            allowed_hosts=["*"],
            _env_file=None,
        )


def test_redis_prefix_rejects_cluster_hash_tag_braces() -> None:
    """配置前缀不能覆盖 Refresh Session Service 自己管理的 Redis hash tag。"""

    with pytest.raises(ValidationError, match="hash tag braces"):
        Settings(
            database_url="sqlite+aiosqlite://",
            secret_key=SecretStr("development-secret"),
            redis_key_prefix="blog:{shared}",
            _env_file=None,
        )


def test_existing_mail_env_aliases_are_used_and_minutes_are_converted(tmp_path) -> None:
    """兼容项目已有 MAIL_* 配置，并把验证码分钟数转换为 Service 使用的秒数。"""

    env_file = tmp_path / "mail.env"
    env_file.write_text(
        """ENV=development
DATABASE_URL=sqlite+aiosqlite:///./test.db
SECRET_KEY=test-development-secret
MAIL_HOST=smtp.example.test
MAIL_PORT=587
MAIL_USE_SSL=false
MAIL_USERNAME=sender@example.test
MAIL_PASSWORD=test-authorization-code
MAIL_FROM=sender@example.test
MAIL_FROM_NAME=三碗博客 Test
PASSWORD_RESET_EXPIRE_MINUTES=15
""",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.smtp_host == "smtp.example.test"
    assert settings.smtp_port == 587
    assert settings.smtp_use_ssl is False
    assert settings.smtp_username == "sender@example.test"
    assert settings.smtp_from_email == "sender@example.test"
    assert settings.smtp_from_name == "三碗博客 Test"
    assert settings.smtp_password is not None
    assert settings.smtp_password.get_secret_value() == "test-authorization-code"
    assert settings.password_reset_code_ttl_seconds == 900
