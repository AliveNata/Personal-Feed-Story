import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.cache import FeedCache, RedisFeedCache, feed_cache_key
from app.config import get_settings
from app.errors import SourceError
from app.media import ALLOWED_CONTENT_TYPE_PREFIXES, is_allowed_media_url, referer_for
from app.models import FeedResponse, Platform
from app.sources import FeedSource
from app.sources.instagram import InstagramSource, build_loader
from app.sources.tiktok import BROWSER_USER_AGENT, TikTokSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

MAX_MEDIA_BYTES = 15 * 1024 * 1024
USERNAME_PATTERN = r"^@?[A-Za-z0-9._-]{1,100}$"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    redis = Redis.from_url(settings.redis_url)

    app.state.http_client = http_client
    app.state.cache = RedisFeedCache(redis, settings.cache_ttl_seconds)
    app.state.sources = {
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
    try:
        yield
    finally:
        await http_client.aclose()
        await redis.aclose()


app = FastAPI(title="Personal Feed Story API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.exception_handler(SourceError)
async def handle_source_error(_request: Request, exc: SourceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


def get_sources(request: Request) -> dict[str, FeedSource]:
    sources: dict[str, FeedSource] = request.app.state.sources
    return sources


def get_cache(request: Request) -> FeedCache:
    cache: FeedCache = request.app.state.cache
    return cache


def get_http_client(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/feed", response_model=FeedResponse, response_model_by_alias=True)
async def get_feed(
    platform: Platform,
    username: Annotated[str, Query(pattern=USERNAME_PATTERN)],
    sources: Annotated[dict[str, FeedSource], Depends(get_sources)],
    cache: Annotated[FeedCache, Depends(get_cache)],
    limit: Annotated[int | None, Query(gt=0, le=50)] = None,
    refresh: bool = False,
) -> FeedResponse:
    username = username.removeprefix("@")
    post_limit = limit or get_settings().default_post_limit
    key = feed_cache_key(platform, username, post_limit)

    if not refresh and (cached := await cache.get(key)) is not None:
        return FeedResponse(platform=platform, username=username, posts=cached, cached=True)

    posts = await sources[platform].fetch(username, post_limit)
    await cache.set(key, posts)
    logger.info("fetched %d posts for %s/%s", len(posts), platform, username)
    return FeedResponse(platform=platform, username=username, posts=posts, cached=False)


@app.get("/api/media")
async def proxy_media(
    url: str,
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> Response:
    if not is_allowed_media_url(url):
        raise HTTPException(status_code=400, detail="Media host tidak diizinkan")

    headers = {"User-Agent": BROWSER_USER_AGENT, "Referer": referer_for(url)}
    try:
        upstream = await http_client.get(url, headers=headers, follow_redirects=False)
    except httpx.HTTPError as exc:
        logger.warning("media fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Gagal mengambil media") from exc

    content_type = upstream.headers.get("content-type", "")
    if upstream.status_code != 200 or not content_type.startswith(ALLOWED_CONTENT_TYPE_PREFIXES):
        # CDN URLs are signed and expire, so a stale cache entry shows up here.
        raise HTTPException(status_code=502, detail=f"CDN membalas {upstream.status_code}")
    if len(upstream.content) > MAX_MEDIA_BYTES:
        raise HTTPException(status_code=413, detail="Media terlalu besar")

    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )
