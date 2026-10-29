"""Request budget per platform, shared by backend and worker through Redis.

The limits are our own conservative guesses, not published platform numbers. The point is to
stop and tell the user to wait before the platform starts answering 429 or flags the account.
"""

import logging
import math
import time
import uuid
from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError

from app.errors import RateLimitedError

logger = logging.getLogger(__name__)

# Show the "slow down" notice once this share of the budget is used.
NEAR_LIMIT_RATIO = 0.8


@dataclass(frozen=True)
class RateStatus:
    used: int
    limit: int
    window_seconds: int
    wait_seconds: int

    @property
    def near_limit(self) -> bool:
        return self.wait_seconds > 0 or self.used >= self.limit * NEAR_LIMIT_RATIO


def wait_message(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    return f"Biar akun nggak kena blokir, tunggu sekitar {minutes} menit sebelum scrape lagi"


class RateBudget:
    """Sliding window counter per platform, plus a cooldown set after a real 429.

    Sync on purpose: instaloader runs in worker threads and calls it from its rate controller.
    """

    def __init__(self, redis: Redis, limits: dict[str, tuple[int, int]]) -> None:
        self._redis = redis
        self._limits = limits

    def _keys(self, platform: str) -> tuple[str, str]:
        return f"ratebudget:{platform}:hits", f"ratebudget:{platform}:cooldown"

    def status(self, platform: str) -> RateStatus:
        limit, window = self._limits[platform]
        hits_key, cooldown_key = self._keys(platform)
        now = time.time()
        try:
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(hits_key, 0, now - window)
            pipe.zcard(hits_key)
            pipe.zrange(hits_key, 0, 0, withscores=True)
            pipe.ttl(cooldown_key)
            _, used, oldest, cooldown_ttl = pipe.execute()
        except RedisError as exc:
            # Without Redis we cannot count, so do not block scraping because of it.
            logger.warning("rate budget unavailable for %s: %s", platform, exc)
            return RateStatus(0, limit, window, 0)

        wait = max(int(cooldown_ttl), 0)
        if used >= limit and oldest:
            wait = max(wait, math.ceil(oldest[0][1] + window - now))
        return RateStatus(int(used), limit, window, wait)

    def ensure_available(self, platform: str, cost: int = 1) -> RateStatus:
        status = self.status(platform)
        if status.wait_seconds > 0 or status.used + cost > status.limit:
            wait = status.wait_seconds or 60
            raise RateLimitedError(wait_message(wait), retry_after=wait)
        return status

    def wait_for_share(self, platform: str, cost: int, share: float) -> int:
        """Seconds until `cost` more requests fit inside `share` of the budget, 0 when they do.

        For heavy optional features (reels): they stop early so the rest of the budget is kept
        for feeds and downloads.
        """
        limit, window = self._limits[platform]
        hits_key, _ = self._keys(platform)
        status = self.status(platform)
        allowed = math.floor(limit * share)
        if status.wait_seconds > 0:
            return status.wait_seconds
        if cost > allowed:
            # Can never fit, waiting would not help either.
            return window
        overflow = status.used + cost - allowed
        if overflow <= 0:
            return 0
        try:
            # The overflow-th oldest hit has to expire before there is room.
            oldest = self._redis.zrange(hits_key, overflow - 1, overflow - 1, withscores=True)
        except RedisError as exc:
            logger.warning("rate budget unavailable for %s: %s", platform, exc)
            return 0
        if not oldest:
            return 0
        expires_at = float(oldest[0][1])  # type: ignore[index]
        return max(1, math.ceil(expires_at + window - time.time()))

    def record(self, platform: str, count: int = 1) -> None:
        _, window = self._limits[platform]
        hits_key, _ = self._keys(platform)
        now = time.time()
        try:
            self._redis.zadd(hits_key, {f"{now}:{uuid.uuid4().hex}": now for _ in range(count)})
            self._redis.expire(hits_key, window)
        except RedisError as exc:
            logger.warning("rate budget record failed for %s: %s", platform, exc)

    def cooldown(self, platform: str, seconds: int) -> None:
        _, cooldown_key = self._keys(platform)
        try:
            self._redis.set(cooldown_key, "1", ex=max(seconds, 1))
        except RedisError as exc:
            logger.warning("rate budget cooldown failed for %s: %s", platform, exc)
        logger.warning("%s cooldown for %ds", platform, seconds)
