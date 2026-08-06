import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    # 连续无请求超过该时间后，Refresh Token 不能再换取新的 Access Token。一般设置7天 = 7 * 24 * 60
    refresh_idle_timeout_minutes: int = Field(default=30, ge=1)
    # 本地 HTTP 调试设为 False；生产 HTTPS 必须设为 True，防止 Cookie 明文传输。
    auth_cookie_secure: bool = False
    # 开发环境通常使用 DEBUG/INFO，生产环境建议 INFO；设为 WARNING 会隐藏正常访问日志。
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # text 适合人在终端阅读；json 适合 Loki、ELK 和云日志平台按字段查询。
    log_format: LogFormat = "text"
    # 容器生产环境建议保持 False 并采集 stdout；仅单机部署且没有采集器时写文件。
    log_to_file: bool = False
    # 仅 LOG_TO_FILE=true 时生效；其下创建 app 完整链路和 error 故障副本两个目录。
    log_directory: str = "logs"
    # 单个日期文件达到该字节数后生成 .1、.2 等备份，防止高流量日期产生超大文件。
    log_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    # 每个日志通道每天最多保留的文件数量，包含正在写入的 YYYY-MM-DD.log。
    log_files_per_day: int = Field(default=5, ge=2, le=100)
    # 按 UTC 日期保留日志；切换到新日期时自动删除更早的日期文件。
    log_retention_days: int = Field(default=14, ge=1, le=365)

    @model_validator(mode="after")
    def validate_database_for_environment(self) -> "Settings":
        if self.env == "development" and not self.database_url.startswith("sqlite+aiosqlite://"):
            raise ValueError("Development DATABASE_URL must use SQLite with aiosqlite")
        if self.env == "production" and not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Production DATABASE_URL must use PostgreSQL with psycopg")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # Loaded from .env file
