from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    douk_base_url: str = "http://douk:5555"
    tiktok_cookie: SecretStr = SecretStr("")
    # DouK pages an account until it passes this many days back, so this bounds request count.
    tiktok_lookback_days: int = Field(default=30, gt=0)

    ig_username: str = ""
    ig_session_file: Path | None = None
    ig_include_stories: bool = True

    redis_url: str = "redis://redis:6379/0"
    cache_ttl_seconds: int = Field(default=900, gt=0)
    default_post_limit: int = Field(default=12, gt=0, le=50)
    http_timeout_seconds: float = 30.0

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
