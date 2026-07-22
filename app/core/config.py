import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMON_ENV_FILE = PROJECT_ROOT / ".env"

Environment = Literal["development", "production"]


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
    secret_key: SecretStr
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    @model_validator(mode="after")
    def validate_database_for_environment(self) -> "Settings":
        if self.env == "development" and not self.database_url.startswith("sqlite:"):
            raise ValueError("Development DATABASE_URL must use SQLite")
        if self.env == "production" and not self.database_url.startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            raise ValueError("Production DATABASE_URL must use PostgreSQL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # Loaded from .env file
