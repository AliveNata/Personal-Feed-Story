import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, cast
from urllib.parse import urlparse

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from curl_cffi.requests import AsyncSession
from curl_cffi.requests import Response as CdnResponse
from curl_cffi.requests.exceptions import RequestException
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from redis import Redis as SyncRedis
from redis.asyncio import Redis
from starlette.background import BackgroundTask

from app import auth, freshness, session_routes
from app.cache import PageCache, PgPageCache, page_key
from app.config import get_settings
from app.db import build_engine, build_session_factory
from app.errors import SourceError
from app.factory import build_sources, build_storage
from app.favorites import routes as favorites_routes
from app.library import routes as library_routes
from app.library.types import MediaResolver
from app.media import ALLOWED_CONTENT_TYPE_PREFIXES, is_allowed_media_url, referer_for
from app.mediacache import MAX_CACHED_IMAGE_BYTES, MediaFileCache
from app.models import (
    FeedPage,
    FeedResponse,
    FreshMediaResponse,
    HighlightItemsResponse,
    HighlightsResponse,
    Platform,
    RateInfo,
    ReelsResponse,
    StoriesResponse,
    proxied_media_url,
)
from app.ratelimit import RateBudget
from app.sources import FeedSource, ProfileProber
from app.sources.instagram import InstagramSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

USERNAME_PATTERN = r"^@?[A-Za-z0-9._-]{1,100}$"
CURSOR_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
# Headers passed through from the CDN so the browser can seek inside videos.
PASSTHROUGH_MEDIA_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")
# Kept on disk for a month, the browser may keep it for a day.
CACHED_MEDIA_HEADERS = {"Cache-Control": "public, max-age=86400"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    redis = Redis.from_url(settings.redis_url)
    sync_redis = SyncRedis.from_url(settings.redis_url)
    engine = build_engine(settings.database_url)
    queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    session_factory = build_session_factory(engine)
    sources = build_sources(settings, http_client, sync_redis, session_factory)
    media_session: AsyncSession[CdnResponse] = AsyncSession(impersonate="chrome")

    app.state.http_client = http_client
    app.state.media_session = media_session
    app.state.redis = redis
    app.state.session_factory = session_factory
    app.state.platform_sessions = sources
    app.state.queue = queue
    app.state.storage = build_storage(settings)
    app.state.cache = PgPageCache(app.state.session_factory)
    app.state.media_cache = MediaFileCache(settings.library_dir / "mediacache")
    app.state.budget = sources.budget
    app.state.sources = {"tiktok": sources.tiktok, "instagram": sources.instagram}
    try:
        yield
    finally:
        await http_client.aclose()
        await media_session.close()
        await redis.aclose()
        sync_redis.close()
        await queue.aclose()
        await engine.dispose()


app = FastAPI(title="Personal Feed Story API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
    allow_credentials=True,
)
app.include_router(auth.router)
app.include_router(library_routes.router)
app.include_router(favorites_routes.router)
app.include_router(session_routes.router)


@app.exception_handler(SourceError)
async def handle_source_error(_request: Request, exc: SourceError) -> JSONResponse:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc), "retryAfter": exc.retry_after},
        headers=headers,
    )


def get_sources(request: Request) -> dict[str, FeedSource]:
    sources: dict[str, FeedSource] = request.app.state.sources
    return sources


def get_instagram(request: Request) -> InstagramSource:
    source: InstagramSource = request.app.state.sources["instagram"]
    return source


def get_cache(request: Request) -> PageCache:
    cache: PageCache = request.app.state.cache
    return cache


def get_budget(request: Request) -> RateBudget | None:
    budget: RateBudget | None = getattr(request.app.state, "budget", None)
    return budget


def get_media_session(request: Request) -> AsyncSession[CdnResponse]:
    session: AsyncSession[CdnResponse] = request.app.state.media_session
    return session


def rate_info(budget: RateBudget | None, platform: str) -> RateInfo | None:
    if budget is None:
        return None
    status = budget.status(platform)
    return RateInfo(
        used=status.used,
        limit=status.limit,
        window_seconds=status.window_seconds,
        wait_seconds=status.wait_seconds,
        near_limit=status.near_limit,
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/feed", response_model=FeedResponse, response_model_by_alias=True)
async def get_feed(
    platform: Platform,
    username: Annotated[str, Query(pattern=USERNAME_PATTERN)],
    sources: Annotated[dict[str, FeedSource], Depends(get_sources)],
    cache: Annotated[PageCache, Depends(get_cache)],
    budget: Annotated[RateBudget | None, Depends(get_budget)],
    cursor: Annotated[str | None, Query(pattern=CURSOR_PATTERN)] = None,
    refresh: bool = False,
) -> FeedResponse:
    username = username.removeprefix("@")
    source = sources[platform]

    if cursor is None:
        first = await freshness.first_page(
            platform, username, source, cast(ProfileProber, source), cache, refresh=refresh
        )
        page, from_cache = first.page, first.from_cache
        fetched_at, checked_at = first.fetched_at, first.checked_at
    else:
        # Older pages do not change often enough to check, they follow the first page's cache.
        key = page_key("feed", platform, username, cursor)
        cached = None if refresh else await cache.get(key, FeedPage)
        if cached is not None:
            page, from_cache, fetched_at = cached.value, True, cached.fetched_at
        else:
            page = await source.fetch_page(username, cursor)
            await cache.set(key, platform, username, page)
            from_cache, fetched_at = False, datetime.now(UTC)
        checked_at = None
    if not from_cache:
        logger.info(
            "fetched %d items for %s/%s cursor=%s", len(page.posts), platform, username, cursor
        )

    return FeedResponse(
        platform=platform,
        username=username,
        posts=page.posts,
        cached=from_cache,
        next_cursor=page.next_cursor,
        stories_status=page.stories_status,
        rate=rate_info(budget, platform),
        fetched_at=fetched_at,
        checked_at=checked_at,
    )


# Stories change through the day: a cached list is reused only this long.
STORIES_CACHE_SECONDS = 10 * 60


@app.get("/api/instagram/stories", response_model=StoriesResponse)
async def list_stories(
    username: Annotated[str, Query(pattern=USERNAME_PATTERN)],
    instagram: Annotated[InstagramSource, Depends(get_instagram)],
    cache: Annotated[PageCache, Depends(get_cache)],
    budget: Annotated[RateBudget | None, Depends(get_budget)],
    refresh: bool = False,
) -> StoriesResponse:
    username = username.removeprefix("@")
    key = page_key("stories", "instagram", username)
    cached = None if refresh else await cache.get(key, StoriesResponse)
    now = datetime.now(UTC)
    if cached is not None and (now - cached.fetched_at).total_seconds() < STORIES_CACHE_SECONDS:
        return cached.value.model_copy(
            update={
                "cached": True,
                "fetched_at": cached.fetched_at,
                "rate": rate_info(budget, "instagram"),
            }
        )
    status, items = await instagram.fetch_stories(username)
    response = StoriesResponse(status=status, items=items, fetched_at=now)
    if status != "login_required":
        await cache.set(key, "instagram", username, response)
    return response.model_copy(update={"rate": rate_info(budget, "instagram")})


@app.get("/api/instagram/reels", response_model=ReelsResponse)
async def list_reels(
    username: Annotated[str, Query(pattern=USERNAME_PATTERN)],
    instagram: Annotated[InstagramSource, Depends(get_instagram)],
    cache: Annotated[PageCache, Depends(get_cache)],
    budget: Annotated[RateBudget | None, Depends(get_budget)],
    cursor: Annotated[str | None, Query(pattern=CURSOR_PATTERN)] = None,
    refresh: bool = False,
) -> ReelsResponse:
    username = username.removeprefix("@")
    # The most expensive Instagram call here: a month in the cache, dropped when the feed
    # check sees the account change.
    key = page_key("reels", "instagram", username, cursor)
    cached = None if refresh else await cache.get(key, FeedPage)
    if cached is not None:
        return ReelsResponse(
            status="available" if cached.value.posts or cursor else "none",
            reels=cached.value.posts,
            next_cursor=cached.value.next_cursor,
            cached=True,
            rate=rate_info(budget, "instagram"),
        )
    status, reels, next_cursor = await instagram.fetch_reels(username, cursor)
    if status != "login_required":
        await cache.set(
            key,
            "instagram",
            username,
            FeedPage(posts=reels, next_cursor=next_cursor, stories_status="none"),
        )
    return ReelsResponse(
        status=status,
        reels=reels,
        next_cursor=next_cursor,
        rate=rate_info(budget, "instagram"),
    )


@app.get("/api/instagram/highlights", response_model=HighlightsResponse)
async def list_highlights(
    username: Annotated[str, Query(pattern=USERNAME_PATTERN)],
    instagram: Annotated[InstagramSource, Depends(get_instagram)],
    cache: Annotated[PageCache, Depends(get_cache)],
    budget: Annotated[RateBudget | None, Depends(get_budget)],
) -> HighlightsResponse:
    username = username.removeprefix("@")
    key = page_key("highlights", "instagram", username)
    cached = await cache.get(key, HighlightsResponse)
    if cached is not None:
        return cached.value.model_copy(
            update={"cached": True, "rate": rate_info(budget, "instagram")}
        )
    status, highlights = await instagram.list_highlights(username)
    response = HighlightsResponse(status=status, highlights=highlights)
    if status != "login_required":
        await cache.set(key, "instagram", username, response)
    return response.model_copy(update={"rate": rate_info(budget, "instagram")})


@app.get("/api/instagram/highlights/{highlight_id}", response_model=HighlightItemsResponse)
async def highlight_items(
    highlight_id: Annotated[str, Path(pattern=r"^\d{1,30}$")],
    username: Annotated[str, Query(pattern=USERNAME_PATTERN)],
    instagram: Annotated[InstagramSource, Depends(get_instagram)],
    cache: Annotated[PageCache, Depends(get_cache)],
    budget: Annotated[RateBudget | None, Depends(get_budget)],
) -> HighlightItemsResponse:
    username = username.removeprefix("@")
    key = page_key("highlight-items", "instagram", username, highlight_id)
    cached = await cache.get(key, HighlightItemsResponse)
    if cached is not None:
        return cached.value.model_copy(
            update={"cached": True, "rate": rate_info(budget, "instagram")}
        )
    items = await instagram.highlight_items(username, highlight_id)
    response = HighlightItemsResponse(items=items)
    await cache.set(key, "instagram", username, response)
    return response.model_copy(update={"rate": rate_info(budget, "instagram")})


@app.get("/api/media/fresh", response_model=FreshMediaResponse)
async def fresh_media(
    platform: Platform,
    username: Annotated[str, Query(pattern=USERNAME_PATTERN)],
    post_id: Annotated[str, Query(alias="postId", pattern=r"^[A-Za-z0-9_-]{1,100}$")],
    expired_url: Annotated[str, Query(alias="expiredUrl")],
    sources: Annotated[dict[str, FeedSource], Depends(get_sources)],
    budget: Annotated[RateBudget | None, Depends(get_budget)],
) -> FreshMediaResponse:
    """New links for a cached post whose video link expired. Costs a request, so only on play."""
    resolver = cast(MediaResolver, sources[platform])
    username = username.removeprefix("@")
    resolved = await resolver.resolve_media(username, post_id)
    if expired_url in resolved.media_urls:
        # The download cache still held the dead link, ask the platform itself.
        resolved = await resolver.resolve_media(username, post_id, fresh=True)
    return FreshMediaResponse(
        urls=[proxied_media_url(url) for url in resolved.media_urls],
        rate=rate_info(budget, platform),
    )


@app.get("/api/media")
async def proxy_media(
    url: str,
    request: Request,
    session: Annotated[AsyncSession[CdnResponse], Depends(get_media_session)],
) -> Response:
    if not is_allowed_media_url(url):
        raise HTTPException(status_code=400, detail="Media host tidak diizinkan")

    media_cache: MediaFileCache = request.app.state.media_cache
    range_header = request.headers.get("range")
    if not range_header and (hit := await asyncio.to_thread(media_cache.get, url)):
        path, content_type = hit
        return FileResponse(path, media_type=content_type, headers=CACHED_MEDIA_HEADERS)

    headers = {"Referer": referer_for(url)}
    if range_header:
        headers["Range"] = range_header
    host = urlparse(url).hostname or ""
    if host.endswith(".tiktok.com"):
        # TikTok's video hosts need the session cookies, the image CDNs do not.
        # The current one: a cookie pasted on the Session page replaces the .env value.
        cookie = request.app.state.sources["tiktok"].cookie
        if cookie:
            headers["Cookie"] = cookie

    try:
        upstream = await session.get(url, headers=headers, stream=True, timeout=60)
    except RequestException as exc:
        logger.warning("media fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Gagal mengambil media") from exc

    content_type = upstream.headers.get("content-type", "")
    if upstream.status_code not in (200, 206) or not content_type.startswith(
        ALLOWED_CONTENT_TYPE_PREFIXES
    ):
        await upstream.aclose()  # type: ignore[no-untyped-call]
        # CDN URLs are signed and expire, so a stale cache entry shows up here.
        raise HTTPException(status_code=502, detail=f"CDN membalas {upstream.status_code}")

    length = upstream.headers.get("content-length", "")
    if (
        upstream.status_code == 200
        and content_type.startswith("image/")
        and length.isdigit()
        and int(length) <= MAX_CACHED_IMAGE_BYTES
    ):
        try:
            body = b"".join([chunk async for chunk in upstream.aiter_content()])  # type: ignore[no-untyped-call]
        finally:
            await upstream.aclose()  # type: ignore[no-untyped-call]
        await asyncio.to_thread(media_cache.put, url, body, content_type)
        return Response(content=body, media_type=content_type, headers=CACHED_MEDIA_HEADERS)

    passthrough = {
        name: value
        for name in PASSTHROUGH_MEDIA_HEADERS
        if (value := upstream.headers.get(name)) is not None
    }
    passthrough["Cache-Control"] = "public, max-age=3600"
    return StreamingResponse(
        upstream.aiter_content(),  # type: ignore[no-untyped-call]
        status_code=upstream.status_code,
        headers=passthrough,
        background=BackgroundTask(upstream.aclose),
    )
