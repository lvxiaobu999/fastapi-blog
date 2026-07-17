import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
