import logging
from typing import Protocol

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models import FeedPage

logger = logging.getLogger(__name__)
_page_adapter = TypeAdapter(FeedPage)


class FeedCache(Protocol):
    async def get(self, key: str) -> FeedPage | None: ...
    async def set(self, key: str, page: FeedPage) -> None: ...


def feed_cache_key(platform: str, username: str, cursor: str | None) -> str:
    return f"feedpage:{platform}:{username.lower()}:{cursor or 'first'}"


class RedisFeedCache:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def get(self, key: str) -> FeedPage | None:
        try:
            raw = await self._redis.get(key)
        except RedisError as exc:
            # A cache outage should degrade to live scraping, not take the API down.
            logger.warning("redis get failed for %s: %s", key, exc)
            return None
        if raw is None:
            return None
        return _page_adapter.validate_json(raw)

    async def set(self, key: str, page: FeedPage) -> None:
        try:
            await self._redis.set(key, _page_adapter.dump_json(page), ex=self._ttl_seconds)
        except RedisError as exc:
            logger.warning("redis set failed for %s: %s", key, exc)
