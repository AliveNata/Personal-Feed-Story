"""Builds the platform sources the same way for the API process and the worker."""

from dataclasses import dataclass

import httpx
from redis import Redis

from app.config import Settings
from app.library.storage import LibraryStorage
from app.ratelimit import RateBudget
from app.sources.instagram import CursorStore, InstagramSource, MediaCache, build_loader
from app.sources.tiktok import TikTokSource


@dataclass(frozen=True)
class Sources:
    tiktok: TikTokSource
    instagram: InstagramSource
    budget: RateBudget


def build_sources(settings: Settings, http_client: httpx.AsyncClient, redis: Redis) -> Sources:
    budget = RateBudget(
        redis,
        {
            "instagram": settings.instagram_rate_budget,
            "tiktok": settings.tiktok_rate_budget,
        },
    )
    tiktok = TikTokSource(
        http_client,
        settings.douk_base_url,
        settings.tiktok_cookie.get_secret_value(),
        settings.tiktok_lookback_days,
        budget=budget,
    )
    instagram = InstagramSource(
        build_loader(settings.ig_username, settings.ig_session_file, budget),
        settings.ig_include_stories,
        page_size=settings.instagram_page_size,
        budget=budget,
        cursors=CursorStore(redis),
        media_cache=MediaCache(redis),
    )
    return Sources(tiktok=tiktok, instagram=instagram, budget=budget)


def build_storage(settings: Settings) -> LibraryStorage:
    return LibraryStorage(
        settings.library_dir,
        compress_photos=settings.compress_photos,
        photo_webp_quality=settings.photo_webp_quality,
        transcode_videos=settings.transcode_videos,
        video_crf=settings.video_crf,
    )
