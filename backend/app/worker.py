"""arq worker for download jobs. Run with: arq app.worker.WorkerSettings"""

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx
from arq import cron, func
from arq.connections import RedisSettings
from redis import Redis

from app.cache import purge_expired
from app.config import get_settings
from app.db import build_engine, build_session_factory
from app.factory import build_sources, build_storage
from app.favorites import service as favorites
from app.library.archive import ImportStore, run_import
from app.library.jobs import JobRunner
from app.library.progress import RedisProgress
from app.library.subtitles import (
    ClaudeTranslator,
    GroqTranscriber,
    SubtitleFiles,
    SubtitleStatus,
    TranslationTarget,
    build_translator,
    run_transcription,
    run_translation,
)
from app.library.types import MediaResolver
from app.mediacache import MediaFileCache
from app.sources.instagram import ANON_BUDGET_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    redis = Redis.from_url(settings.redis_url)
    sources = build_sources(settings, http_client, redis, build_session_factory(engine))
    resolvers: dict[str, MediaResolver] = {
        "tiktok": sources.tiktok,
        "instagram": sources.instagram,
    }
    ctx["redis"] = redis
    ctx["sources"] = sources
    ctx["session_factory"] = build_session_factory(engine)
    ctx["media_cache"] = MediaFileCache(settings.library_dir / "mediacache")
    ctx["import_store"] = ImportStore(redis)
    ctx["storage"] = build_storage(settings)
    ctx["delays"] = {
        "instagram": settings.instagram_delay_seconds,
        "tiktok": settings.tiktok_delay_seconds,
    }
    ctx["engine"] = engine
    ctx["subtitle_files"] = SubtitleFiles(settings.library_dir)
    ctx["subtitle_status"] = SubtitleStatus(redis)
    ctx["transcriber"] = GroqTranscriber(
        http_client,
        settings.groq_api_key.get_secret_value(),
        settings.groq_transcribe_model,
        settings.groq_base_url,
    )
    ctx["translator"] = build_translator(settings, http_client)
    # Asked for per video, to make a Groq translation more natural.
    ctx["claude"] = ClaudeTranslator(
        http_client,
        settings.anthropic_api_key.get_secret_value(),
        settings.claude_translate_model,
        settings.anthropic_base_url,
    )
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


# Instagram favorites are checked less often than the 2 hour cron, to spare the account.
INSTAGRAM_CHECK_INTERVAL = timedelta(hours=6)


async def check_favorites(ctx: dict[str, Any], force: bool = False) -> None:
    redis: Redis = ctx["redis"]
    sources = ctx["sources"]
    redis.set(favorites.CHECKING_KEY, "1", ex=favorites.CHECKING_TTL_SECONDS)
    try:
        await favorites.check_all(
            ctx["session_factory"],
            {"tiktok": sources.tiktok, "instagram": sources.instagram},
            sources.budget,
            ctx["delays"],
            # "Cek sekarang" checks everything, the cron respects the interval.
            min_interval=None if force else {"instagram": INSTAGRAM_CHECK_INTERVAL},
            budget_keys={"instagram": ANON_BUDGET_KEY},
        )
    finally:
        redis.delete(favorites.CHECKING_KEY)


async def purge_caches(ctx: dict[str, Any]) -> None:
    pages = await purge_expired(ctx["session_factory"])
    files = await asyncio.to_thread(ctx["media_cache"].purge)
    logger.info("cache purge: %d expired pages, %d old media files", pages, files)


async def import_archive(ctx: dict[str, Any], import_id: str) -> None:
    states: ImportStore = ctx["import_store"]
    raw = states.load(import_id)
    if raw is None:
        logger.warning("archive import %s has no state, skipped", import_id)
        return
    settings = get_settings()
    await run_import(
        import_id,
        Path(raw["path"]),
        raw["params"],
        ctx["storage"],
        ctx["session_factory"],
        states,
        settings.max_archive_unpacked_bytes,
    )


async def transcribe_subtitles(ctx: dict[str, Any], sha256: str, video_path: str) -> None:
    await run_transcription(
        sha256,
        Path(video_path),
        ctx["subtitle_files"],
        ctx["subtitle_status"],
        ctx["transcriber"],
        get_settings().subtitle_chunk_seconds,
    )


async def translate_subtitles(
    ctx: dict[str, Any], sha256: str, target: TranslationTarget, use_claude: bool = False
) -> None:
    translator = ctx["claude"] if use_claude else ctx["translator"]
    await run_translation(sha256, target, ctx["subtitle_files"], ctx["subtitle_status"], translator)


class WorkerSettings:
    functions: ClassVar = [
        download_job,
        import_archive,
        # No stored result, so "Cek sekarang" can reuse the fixed job id right after a run.
        func(check_favorites, keep_result=0),
        # No stored result either, so a failed subtitle can be generated again right away.
        func(transcribe_subtitles, keep_result=0),
        func(translate_subtitles, keep_result=0),
    ]
    cron_jobs: ClassVar = [
        # Every 2 hours: frequent enough for a hint, light on the request budget.
        cron(check_favorites, hour=set(range(0, 24, 2)), minute=17, keep_result=0),
        # Once a day at night is plenty for month long caches.
        cron(purge_caches, hour=3, minute=41, keep_result=0),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # One job at a time keeps request volume per account low.
    max_jobs = 1
    job_timeout = 3 * 60 * 60
