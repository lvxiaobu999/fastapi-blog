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
    common_values = dotenv_values(COMMON_ENV_FILE)
    return os.getenv("ENV") or common_values.get("ENV") or "development"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(COMMON_ENV_FILE, PROJECT_ROOT / f".env.{_selected_environment()}"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Environment = "development"
    project_title: str = "FastAPI Blog"
    database_url: str
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
