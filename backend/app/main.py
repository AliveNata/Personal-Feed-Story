import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import urlparse

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from curl_cffi.requests import AsyncSession, Response
from curl_cffi.requests.exceptions import RequestException
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from redis import Redis as SyncRedis
from redis.asyncio import Redis
from starlette.background import BackgroundTask

from app import auth
from app.cache import FeedCache, RedisFeedCache, feed_cache_key
from app.config import get_settings
from app.db import build_engine, build_session_factory
from app.errors import SourceError
from app.factory import build_sources, build_storage
from app.library import routes as library_routes
from app.media import ALLOWED_CONTENT_TYPE_PREFIXES, is_allowed_media_url, referer_for
from app.models import (
    FeedPage,
    FeedResponse,
    HighlightItemsResponse,
    HighlightsResponse,
    Platform,
    RateInfo,
)
from app.ratelimit import RateBudget
from app.sources import FeedSource
from app.sources.instagram import InstagramSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

USERNAME_PATTERN = r"^@?[A-Za-z0-9._-]{1,100}$"
CURSOR_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
# Headers passed through from the CDN so the browser can seek inside videos.
PASSTHROUGH_MEDIA_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    redis = Redis.from_url(settings.redis_url)
    sync_redis = SyncRedis.from_url(settings.redis_url)
    engine = build_engine(settings.database_url)
    queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    sources = build_sources(settings, http_client, sync_redis)
    media_session: AsyncSession[Response] = AsyncSession(impersonate="chrome")

    app.state.http_client = http_client
    app.state.media_session = media_session
    app.state.redis = redis
    app.state.session_factory = build_session_factory(engine)
    app.state.queue = queue
    app.state.storage = build_storage(settings)
    app.state.cache = RedisFeedCache(redis, settings.cache_ttl_seconds)
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
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
    allow_credentials=True,
)
app.include_router(auth.router)
app.include_router(library_routes.router)


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


def get_cache(request: Request) -> FeedCache:
    cache: FeedCache = request.app.state.cache
    return cache


def get_budget(request: Request) -> RateBudget | None:
    budget: RateBudget | None = getattr(request.app.state, "budget", None)
    return budget


def get_media_session(request: Request) -> AsyncSession[Response]:
    session: AsyncSession[Response] = request.app.state.media_session
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
    cache: Annotated[FeedCache, Depends(get_cache)],
    budget: Annotated[RateBudget | None, Depends(get_budget)],
    cursor: Annotated[str | None, Query(pattern=CURSOR_PATTERN)] = None,
    refresh: bool = False,
) -> FeedResponse:
    username = username.removeprefix("@")
    key = feed_cache_key(platform, username, cursor)

    cached_page = None if refresh else await cache.get(key)
    page: FeedPage
    if cached_page is not None:
        page = cached_page
    else:
        page = await sources[platform].fetch_page(username, cursor)
        await cache.set(key, page)
        logger.info(
            "fetched %d items for %s/%s cursor=%s", len(page.posts), platform, username, cursor
        )

    return FeedResponse(
        platform=platform,
        username=username,
        posts=page.posts,
        cached=cached_page is not None,
        next_cursor=page.next_cursor,
        stories_status=page.stories_status,
        rate=rate_info(budget, platform),
    )


@app.get("/api/instagram/highlights", response_model=HighlightsResponse)
async def list_highlights(
    username: Annotated[str, Query(pattern=USERNAME_PATTERN)],
    instagram: Annotated[InstagramSource, Depends(get_instagram)],
    budget: Annotated[RateBudget | None, Depends(get_budget)],
) -> HighlightsResponse:
    status, highlights = await instagram.list_highlights(username.removeprefix("@"))
    return HighlightsResponse(
        status=status,
        highlights=highlights,
        rate=rate_info(budget, "instagram"),
    )


@app.get("/api/instagram/highlights/{highlight_id}", response_model=HighlightItemsResponse)
async def highlight_items(
    highlight_id: Annotated[str, Path(pattern=r"^\d{1,30}$")],
    username: Annotated[str, Query(pattern=USERNAME_PATTERN)],
    instagram: Annotated[InstagramSource, Depends(get_instagram)],
    budget: Annotated[RateBudget | None, Depends(get_budget)],
) -> HighlightItemsResponse:
    items = await instagram.highlight_items(username.removeprefix("@"), highlight_id)
    return HighlightItemsResponse(items=items, rate=rate_info(budget, "instagram"))


@app.get("/api/media")
async def proxy_media(
    url: str,
    request: Request,
    session: Annotated[AsyncSession[Response], Depends(get_media_session)],
) -> StreamingResponse:
    if not is_allowed_media_url(url):
        raise HTTPException(status_code=400, detail="Media host tidak diizinkan")

    headers = {"Referer": referer_for(url)}
    if range_header := request.headers.get("range"):
        headers["Range"] = range_header
    host = urlparse(url).hostname or ""
    if host.endswith(".tiktok.com"):
        # TikTok's video hosts need the session cookies, the image CDNs do not.
        cookie = get_settings().tiktok_cookie.get_secret_value()
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
