"""arq worker for download jobs. Run with: arq app.worker.WorkerSettings"""

import logging
from typing import Any, ClassVar

import httpx
from arq.connections import RedisSettings

from app.config import Settings, get_settings
from app.db import build_engine, build_session_factory
from app.library.jobs import JobRunner
from app.library.storage import LibraryStorage
from app.library.types import MediaResolver
from app.sources.instagram import InstagramSource, build_loader
from app.sources.tiktok import TikTokSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def build_storage(settings: Settings) -> LibraryStorage:
    return LibraryStorage(
        settings.library_dir,
        compress_photos=settings.compress_photos,
        photo_webp_quality=settings.photo_webp_quality,
        transcode_videos=settings.transcode_videos,
        video_crf=settings.video_crf,
    )


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    resolvers: dict[str, MediaResolver] = {
        "tiktok": TikTokSource(
            http_client,
            settings.douk_base_url,
            settings.tiktok_cookie.get_secret_value(),
            settings.tiktok_lookback_days,
        ),
        "instagram": InstagramSource(
            build_loader(settings.ig_username, settings.ig_session_file),
            settings.ig_include_stories,
        ),
    }
    ctx["engine"] = engine
    ctx["http_client"] = http_client
    ctx["runner"] = JobRunner(
        build_session_factory(engine),
        resolvers,
        build_storage(settings),
        {
            "instagram": settings.instagram_delay_seconds,
            "tiktok": settings.tiktok_delay_seconds,
        },
        settings.max_download_bytes,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http_client"].aclose()
    await ctx["engine"].dispose()


async def download_job(ctx: dict[str, Any], job_id: str) -> None:
    runner: JobRunner = ctx["runner"]
    await runner.run(job_id)


class WorkerSettings:
    functions: ClassVar = [download_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # One job at a time keeps request volume per account low.
    max_jobs = 1
    job_timeout = 3 * 60 * 60
