"""Favorite accounts and the cheap probe that tells whether they posted since the last visit."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import Favorite
from app.errors import RateLimitedError, SourceError
from app.ratelimit import RateBudget
from app.sources import ProfileProbe, ProfileProber

logger = logging.getLogger(__name__)

UpdateKind = Literal["post", "story"]

CHECK_JOB_ID = "favorites-check"
CHECKING_KEY = "favorites:checking"
CHECKING_TTL_SECONDS = 15 * 60
# Background checks stop once this share of a platform budget is used, so scraping by hand
# still has room left.
CHECK_BUDGET_SHARE = 0.7
# Requests one probe may cost (Instagram profile plus story reel).
PROBE_COST = 2


def update_kinds(favorite: Favorite) -> list[UpdateKind]:
    kinds: list[UpdateKind] = []
    if (
        favorite.post_count is not None
        and favorite.seen_post_count is not None
        and favorite.post_count > favorite.seen_post_count
    ):
        kinds.append("post")
    if favorite.latest_story_at is not None and (
        favorite.seen_story_at is None or favorite.latest_story_at > favorite.seen_story_at
    ):
        kinds.append("story")
    return kinds


def apply_probe(favorite: Favorite, probe: ProfileProbe, now: datetime) -> None:
    # checked_at is no guide: a failed probe sets it too. Nothing stored yet means no success.
    never_probed = favorite.seen_post_count is None and favorite.seen_story_at is None
    if probe.avatar_url:
        favorite.avatar_url = probe.avatar_url
    favorite.post_count = probe.post_count
    favorite.latest_story_at = probe.latest_story_at
    if never_probed:
        # Whatever exists when the account is added counts as already seen.
        favorite.seen_post_count = probe.post_count
        favorite.seen_story_at = probe.latest_story_at
    elif favorite.seen_post_count is None:
        favorite.seen_post_count = probe.post_count
    favorite.checked_at = now
    favorite.check_error = None


def mark_seen(favorite: Favorite, now: datetime) -> None:
    favorite.seen_post_count = favorite.post_count
    # "now", not latest_story_at: a story posted after the last probe was just seen in the feed.
    favorite.seen_story_at = now


async def probe_favorite(
    favorite: Favorite, prober: ProfileProber, now: datetime | None = None
) -> None:
    """Probe one account and store the result. Errors are stored on the row and re-raised."""
    checked_at = now or datetime.now(UTC)
    try:
        probe = await prober.probe(favorite.username)
    except RateLimitedError:
        raise
    except SourceError as exc:
        favorite.check_error = str(exc)
        favorite.checked_at = checked_at
        raise
    apply_probe(favorite, probe, checked_at)


async def get_favorite(db: AsyncSession, platform: str, username: str) -> Favorite | None:
    favorite: Favorite | None = await db.scalar(
        select(Favorite).where(
            Favorite.platform == platform, func.lower(Favorite.username) == username.lower()
        )
    )
    return favorite


async def list_favorites(db: AsyncSession) -> list[Favorite]:
    favorites = list((await db.scalars(select(Favorite).order_by(Favorite.created_at))).all())
    # Accounts with something new first, the rest in the order they were added.
    return sorted(favorites, key=lambda favorite: not update_kinds(favorite))


@dataclass
class CheckSummary:
    checked: int = 0
    failed: int = 0
    skipped_platforms: set[str] = field(default_factory=set)


def _has_room(budget: RateBudget, platform: str) -> bool:
    status = budget.status(platform)
    return status.wait_seconds == 0 and status.used + PROBE_COST <= status.limit * (
        CHECK_BUDGET_SHARE
    )


async def check_all(
    session_factory: async_sessionmaker[AsyncSession],
    probers: Mapping[str, ProfileProber],
    budget: RateBudget,
    delays: Mapping[str, tuple[float, float]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CheckSummary:
    """Probe every favorite, least recently checked first, within the background budget."""
    summary = CheckSummary()
    async with session_factory() as db:
        favorites = (
            await db.scalars(select(Favorite).order_by(Favorite.checked_at.asc().nulls_first()))
        ).all()
        for favorite in favorites:
            platform = favorite.platform
            if platform in summary.skipped_platforms or platform not in probers:
                continue
            if not await asyncio.to_thread(_has_room, budget, platform):
                logger.info("favorites check: %s budget reserved for scraping, stopping", platform)
                summary.skipped_platforms.add(platform)
                continue
            try:
                await probe_favorite(favorite, probers[platform])
                summary.checked += 1
            except RateLimitedError:
                logger.warning("favorites check: %s rate limited, stopping", platform)
                summary.skipped_platforms.add(platform)
                continue
            except SourceError as exc:
                logger.warning(
                    "favorites check: %s/%s failed: %s", platform, favorite.username, exc
                )
                summary.failed += 1
            await db.commit()
            low, high = delays.get(platform, (1.0, 3.0))
            await sleep(random.uniform(low, high))
    logger.info(
        "favorites check done: %d checked, %d failed, skipped %s",
        summary.checked,
        summary.failed,
        sorted(summary.skipped_platforms) or "none",
    )
    return summary
