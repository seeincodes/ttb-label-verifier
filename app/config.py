from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    extractor_provider: Literal["gemini", "openai"] = Field(default="gemini")

    gemini_api_key: SecretStr = Field(default=SecretStr(""))
    gemini_model: str = Field(default="gemini-2.5-flash")

    openai_api_key: SecretStr = Field(default=SecretStr(""))
    openai_model: str = Field(default="gpt-4o")

    extraction_timeout_seconds: int = Field(default=12, ge=10, le=60)
    batch_concurrency: int = Field(default=5, ge=1, le=20)
    cache_maxsize: int = Field(default=128, ge=1)

    app_env: Literal["development", "production"] = Field(default="development")
    log_level: str = Field(default="INFO")
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000, ge=1, le=65535)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
