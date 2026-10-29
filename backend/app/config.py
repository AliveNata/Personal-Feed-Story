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
    default_post_limit: int = Field(default=12, gt=0, le=50)
    http_timeout_seconds: float = 30.0

    cors_origins: list[str] = ["http://localhost:5173"]

    # Set true on the VPS once it is served over https.
    session_cookie_secure: bool = False

    database_url: str = "postgresql+asyncpg://feed:feed@postgres:5432/feed"
    library_dir: Path = Path("/data/library")
    # Random pause between posts in a download job, to stay under platform rate limits.
    instagram_delay_seconds: tuple[float, float] = (4.0, 9.0)
    tiktok_delay_seconds: tuple[float, float] = (1.0, 3.0)
    # (max requests, window seconds). Conservative guesses, tune if the platform complains.
    instagram_rate_budget: tuple[int, int] = (100, 900)
    tiktok_rate_budget: tuple[int, int] = (150, 900)
    instagram_page_size: int = Field(default=12, gt=0, le=50)
    max_download_bytes: int = Field(default=500 * 1024 * 1024, gt=0)
    max_posts_per_job: int = Field(default=50, gt=0)
    # Lossy, off by default: originals are kept unless disk space becomes the problem.
    compress_photos: bool = False
    photo_webp_quality: int = Field(default=82, ge=1, le=100)
    transcode_videos: bool = False
    video_crf: int = Field(default=28, ge=0, le=51)


@lru_cache
def get_settings() -> Settings:
    return Settings()
