import logging
from typing import Protocol

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models import Post

logger = logging.getLogger(__name__)
_posts_adapter = TypeAdapter(list[Post])


class FeedCache(Protocol):
    async def get(self, key: str) -> list[Post] | None: ...
    async def set(self, key: str, posts: list[Post]) -> None: ...


def feed_cache_key(platform: str, username: str, limit: int) -> str:
    return f"feed:{platform}:{username.lower()}:{limit}"


class RedisFeedCache:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def get(self, key: str) -> list[Post] | None:
        try:
            raw = await self._redis.get(key)
        except RedisError as exc:
            # A cache outage should degrade to live scraping, not take the API down.
            logger.warning("redis get failed for %s: %s", key, exc)
            return None
        if raw is None:
            return None
        return _posts_adapter.validate_json(raw)

    async def set(self, key: str, posts: list[Post]) -> None:
        try:
            await self._redis.set(key, _posts_adapter.dump_json(posts), ex=self._ttl_seconds)
        except RedisError as exc:
            logger.warning("redis set failed for %s: %s", key, exc)
