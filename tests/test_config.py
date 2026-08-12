"""应用配置组合校验测试；不连接数据库、Redis 或其他外部服务。"""

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


def production_settings(*, auth_cookie_secure: bool) -> Settings:
    """构造最小生产配置，供安全组合测试复用。"""

    return Settings(
        env="production",
        database_url="postgresql+psycopg://app:placeholder@db.internal/blog",
        secret_key=SecretStr("test-only-secret-with-at-least-32-characters"),
        redis_url=SecretStr("rediss://cache.internal:6379/0"),
        allowed_hosts=["blog.example.com"],
        auth_cookie_secure=auth_cookie_secure,
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
