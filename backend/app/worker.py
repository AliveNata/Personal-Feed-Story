"""arq worker for download jobs. Run with: arq app.worker.WorkerSettings"""

import logging
from typing import Any, ClassVar

import httpx
from arq import cron, func
from arq.connections import RedisSettings
from redis import Redis

from app.config import get_settings
from app.db import build_engine, build_session_factory
from app.factory import build_sources, build_storage
from app.favorites import service as favorites
from app.library.jobs import JobRunner
from app.library.progress import RedisProgress
from app.library.types import MediaResolver

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    redis = Redis.from_url(settings.redis_url)
    sources = build_sources(settings, http_client, redis)
    resolvers: dict[str, MediaResolver] = {
        "tiktok": sources.tiktok,
        "instagram": sources.instagram,
    }
    ctx["redis"] = redis
    ctx["sources"] = sources
    ctx["session_factory"] = build_session_factory(engine)
    ctx["delays"] = {
        "instagram": settings.instagram_delay_seconds,
        "tiktok": settings.tiktok_delay_seconds,
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
        progress=RedisProgress(redis),
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http_client"].aclose()
    await ctx["engine"].dispose()
    ctx["redis"].close()


async def download_job(ctx: dict[str, Any], job_id: str) -> None:
    runner: JobRunner = ctx["runner"]
    await runner.run(job_id)


async def check_favorites(ctx: dict[str, Any]) -> None:
    redis: Redis = ctx["redis"]
    sources = ctx["sources"]
    redis.set(favorites.CHECKING_KEY, "1", ex=favorites.CHECKING_TTL_SECONDS)
    try:
        await favorites.check_all(
            ctx["session_factory"],
            {"tiktok": sources.tiktok, "instagram": sources.instagram},
            sources.budget,
            ctx["delays"],
        )
    finally:
        redis.delete(favorites.CHECKING_KEY)


class WorkerSettings:
    functions: ClassVar = [
        download_job,
        # No stored result, so "Cek sekarang" can reuse the fixed job id right after a run.
        func(check_favorites, keep_result=0),
    ]
    cron_jobs: ClassVar = [
        # Every 2 hours: frequent enough for a hint, light on the request budget.
        cron(check_favorites, hour=set(range(0, 24, 2)), minute=17, keep_result=0),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # One job at a time keeps request volume per account low.
    max_jobs = 1
    job_timeout = 3 * 60 * 60
