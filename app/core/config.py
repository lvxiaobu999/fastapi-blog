"""集中加载和校验开发/生产环境配置，不负责建立数据库或 Redis 连接。

配置优先级是系统环境变量 > 环境专用文件 > 公共 ``.env`` > 代码默认值。Settings 在应用
启动阶段把字符串转换成明确类型，并拒绝危险组合，让配置错误尽早暴露而不是运行中才报错。
"""

import os
from functools import lru_cache
from ipaddress import IPv4Address, ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMON_ENV_FILE = PROJECT_ROOT / ".env"

Environment = Literal["development", "production"]
LogFormat = Literal["text", "json"]


def _parse_ip(value: str):
    """把 Host 候选解析为 IP；域名或格式错误时返回 ``None``。"""

    try:
        return ip_address(value)
    except ValueError:
        return None


def _is_ipv4(value: str) -> bool:
    """判断 Host 是否为 IPv4 字面量。"""

    return isinstance(_parse_ip(value), IPv4Address)


def _selected_environment() -> str:
    """决定环境专用文件；系统 ENV 覆盖公共 .env，未配置时默认开发环境。"""

    common_values = dotenv_values(COMMON_ENV_FILE)
    return os.getenv("ENV") or common_values.get("ENV") or "development"


class Settings(BaseSettings):
    """应用配置契约；启动时类型或环境组合错误会立即失败。"""

    model_config = SettingsConfigDict(
        # 后面的环境专用文件覆盖公共文件，系统环境变量又会覆盖这两个文件。
        env_file=(COMMON_ENV_FILE, PROJECT_ROOT / f".env.{_selected_environment()}"),
        # 环境文件统一使用 UTF-8，允许 PROJECT_TITLE 等配置包含中文。
        env_file_encoding="utf-8",
        # 环境变量通常使用大写，字段使用小写；关闭大小写敏感可以直接完成映射。
        case_sensitive=False,
        # 不属于当前 Settings 的环境项交给其他组件使用，不因此阻止应用启动。
        extra="ignore",
    )

    env: Environment = "development"
    project_title: str = "FastAPI Blog"
    database_url: str
    # 环境变量中的列表使用 JSON，例如 ALLOWED_HOSTS=["blog.example.com"]。这里不写协议、
    # 端口或路径。TrustedHostMiddleware 只接受列出的 Host，防止伪造 Header 影响跳转、链接
    # 或上游缓存；开发默认包含本机与测试客户端，生产必须显式配置真实域名。
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "::1", "test", "testserver"],
        min_length=1,
    )
    # 即使 JWT 尚未启用，也强制从环境读取密钥，避免后续接入认证时误用空值或硬编码默认值。
    # SECRET_KEY 用 SecretStr 防止配置对象 repr 或校验错误意外打印完整密钥；生产环境
    # 必须由部署平台注入随机值，不能复用仓库或开发机密钥。
    secret_key: SecretStr
    # HS256 使用同一个密钥签名和验证，适合当前单体应用；更换算法必须同步编码和解码端。
    algorithm: str = "HS256"
    # Access Token 只有 30 分钟有效期；缩短会增加重新登录频率，延长会扩大泄露后的风险。
    access_token_expire_minutes: int = Field(default=30, ge=1)
    # Refresh Session 的最长寿命；即使用户持续操作，也不会超过这个绝对上限。一般设置一个月 = 30 * 24 * 60
    refresh_token_expire_minutes: int = Field(default=7 * 24 * 60, ge=1)
    # 现在只在“刷新 Token”时更新活动时间，不让普通 Access JWT 请求依赖 Redis；因此空闲
    # 期限必须显著长于 Access 有效期。默认 24 小时无刷新后要求重新登录。
    refresh_idle_timeout_minutes: int = Field(default=24 * 60, ge=1)
    # 本地 HTTP 调试设为 False；生产 HTTPS 必须设为 True，防止 Cookie 明文传输。
    auth_cookie_secure: bool = False
    # 备案和证书完成前的临时公网 IP HTTP 模式。它只允许显式开启，且必须配合字面 IPv4
    # Host 和非 Secure Cookie 使用；不要在正式域名或 HTTPS 部署中打开，否则 Refresh
    # Token 会以明文 HTTP Cookie 传输。备案/证书完成后应删除该项或恢复为 false。
    public_ip_mode: bool = False
    # Redis 保存有状态 Refresh Session。SecretStr 防止带密码的生产 URL 出现在配置 repr。
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    # Key 前缀隔离同一 Redis 数据库中的不同应用和环境；修改后旧会话会自然失效。
    redis_key_prefix: str = "fastapi-blog"
    # Redis 故障应快速失败并由 API 返回 503，不能让登录/刷新请求长时间挂起。
    redis_socket_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    # 开发环境通常使用 DEBUG/INFO，生产环境建议 INFO；设为 WARNING 会隐藏正常访问日志。
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # text 适合人在终端阅读；json 适合 Loki、ELK 和云日志平台按字段查询。
    log_format: LogFormat = "text"
    # 容器生产环境建议保持 False 并采集 stdout；仅单机部署且没有采集器时写文件。
    log_to_file: bool = False
    # 仅 LOG_TO_FILE=true 时生效；目录内直接生成 YYYY-MM-DD.log 日期文件。
    log_directory: str = "logs"
    # 单个日期文件达到该字节数后生成 .1、.2 等备份，防止高流量日期产生超大文件。
    log_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    # 每天最多保留的文件数量，包含正在写入的 YYYY-MM-DD.log。
    log_files_per_day: int = Field(default=5, ge=2, le=100)
    # 按 UTC 日期保留日志；切换到新日期时自动删除更早的日期文件。
    log_retention_days: int = Field(default=14, ge=1, le=365)

    @model_validator(mode="after")
    def validate_environment_contract(self) -> "Settings":
        """检查单个字段类型无法表达的跨配置安全规则。

        例如 ``auth_cookie_secure`` 单独看只是布尔值，但与 ``env=production`` 组合时必须为
        True（临时 ``PUBLIC_IP_MODE`` 是唯一例外）。校验失败会阻止应用启动，避免带着不完整
        的生产配置继续运行。
        """

        if self.env == "development" and not self.database_url.startswith("sqlite+aiosqlite://"):
            raise ValueError("Development DATABASE_URL must use SQLite with aiosqlite")
        if self.env == "production" and not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Production DATABASE_URL must use PostgreSQL with psycopg")
        # Refresh Token 保存在 Cookie 中。生产流量即使通常由 Nginx 跳转到 HTTPS，漏掉
        # Secure 仍可能让浏览器在跳转前的 HTTP 请求中携带 Cookie，因此必须启动失败。
        if self.env == "production" and not self.auth_cookie_secure and not self.public_ip_mode:
            raise ValueError("Production AUTH_COOKIE_SECURE must be true")
        if self.public_ip_mode:
            if self.env != "production":
                raise ValueError("PUBLIC_IP_MODE requires ENV=production")
            if self.auth_cookie_secure:
                raise ValueError("PUBLIC_IP_MODE requires AUTH_COOKIE_SECURE=false")
        normalized_hosts = [host.strip().lower() for host in self.allowed_hosts]
        if any(
            not host or host == "*" or "://" in host or "/" in host or " " in host
            for host in normalized_hosts
        ):
            raise ValueError(
                "ALLOWED_HOSTS must contain explicit hostnames without schemes or paths"
            )
        self.allowed_hosts = list(dict.fromkeys(normalized_hosts))
        if self.env == "production" and {"test", "testserver"} & set(self.allowed_hosts):
            raise ValueError("Production ALLOWED_HOSTS must be explicitly configured")
        # 只接受 IPv4 字面量，避免把临时 HTTP 模式误用于正式域名。IPv6 还需要在 Nginx
        # listen/server_name、浏览器 URL 和安全组层面分别处理，暂不纳入该开关。
        if self.public_ip_mode and (
            not self.allowed_hosts or any(not _is_ipv4(host) for host in self.allowed_hosts)
        ):
            raise ValueError("PUBLIC_IP_MODE ALLOWED_HOSTS must contain IPv4 addresses only")
        if self.env == "production" and len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("Production SECRET_KEY must contain at least 32 characters")
        if self.refresh_idle_timeout_minutes <= self.access_token_expire_minutes:
            raise ValueError("REFRESH_IDLE_TIMEOUT_MINUTES must exceed ACCESS_TOKEN_EXPIRE_MINUTES")
        if "{" in self.redis_key_prefix or "}" in self.redis_key_prefix:
            # Refresh Session 自己使用花括号控制 Redis Cluster hash slot；允许配置层注入
            # 花括号会改变脚本 Key 的分片结果，使本应原子的 Lua 操作在集群中失败。
            raise ValueError("REDIS_KEY_PREFIX must not contain Redis hash tag braces")
        redis_url = self.redis_url.get_secret_value()
        if not redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        if self.env == "production" and urlsplit(redis_url).hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Production REDIS_URL must be provided by the deployment environment")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # Loaded from .env file
