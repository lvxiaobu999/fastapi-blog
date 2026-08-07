import os
from functools import lru_cache
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
    def validate_database_for_environment(self) -> "Settings":
        if self.env == "development" and not self.database_url.startswith("sqlite+aiosqlite://"):
            raise ValueError("Development DATABASE_URL must use SQLite with aiosqlite")
        if self.env == "production" and not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Production DATABASE_URL must use PostgreSQL with psycopg")
        if self.refresh_idle_timeout_minutes <= self.access_token_expire_minutes:
            raise ValueError("REFRESH_IDLE_TIMEOUT_MINUTES must exceed ACCESS_TOKEN_EXPIRE_MINUTES")
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
