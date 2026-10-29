"""Live download progress of the post a job is working on, kept in Redis instead of Postgres.

It changes many times per second, and nothing needs it once the job is done.
"""

import json
import logging
import time
from typing import Protocol

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

PROGRESS_TTL_SECONDS = 60 * 60
# The UI polls every second, more writes than that would only load Redis.
MIN_REPORT_INTERVAL_SECONDS = 0.5


def progress_key(job_id: str) -> str:
    return f"jobprogress:{job_id}"


class ProgressReporter(Protocol):
    def report(
        self, job_id: str, post_id: str, percent: int | None, *, force: bool = False
    ) -> None: ...

    def clear(self, job_id: str) -> None: ...


class NullProgress:
    def report(
        self, job_id: str, post_id: str, percent: int | None, *, force: bool = False
    ) -> None:
        return None

    def clear(self, job_id: str) -> None:
        return None


class RedisProgress:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._last_report = 0.0

    def report(
        self, job_id: str, post_id: str, percent: int | None, *, force: bool = False
    ) -> None:
        now = time.monotonic()
        if not force and now - self._last_report < MIN_REPORT_INTERVAL_SECONDS:
            return
        self._last_report = now
        payload = json.dumps({"postId": post_id, "percent": percent})
        try:
            self._redis.set(progress_key(job_id), payload, ex=PROGRESS_TTL_SECONDS)
        except RedisError as exc:
            # Only the percentage in the UI is lost, the download itself goes on.
            logger.warning("could not store job progress: %s", exc)

    def clear(self, job_id: str) -> None:
        try:
            self._redis.delete(progress_key(job_id))
        except RedisError as exc:
            logger.warning("could not clear job progress: %s", exc)


async def read_progress(redis: AsyncRedis, job_id: str) -> tuple[str | None, int | None]:
    try:
        raw = await redis.get(progress_key(job_id))
    except RedisError as exc:
        logger.warning("could not read job progress: %s", exc)
        return None, None
    if raw is None:
        return None, None
    data = json.loads(raw)
    percent = data.get("percent")
    return str(data["postId"]), int(percent) if percent is not None else None
