"""Serve the first feed page from the month long cache, scraping only when the account changed.

A cheap probe (post count, newest story) runs at most once per CHECK_INTERVAL per account.
Scraping again only happens when it shows something new, or on "Scrape ulang".
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.cache import CachedEntry, PageCache, Snapshot, page_key
from app.errors import SourceError
from app.models import FeedPage
from app.sources import FeedSource, ProfileProbe, ProfileProber

logger = logging.getLogger(__name__)

CHECK_INTERVAL = timedelta(hours=1)
STORY_LIFETIME = timedelta(hours=24)


@dataclass(frozen=True)
class FirstPage:
    page: FeedPage
    from_cache: bool
    fetched_at: datetime
    checked_at: datetime | None


def has_changed(snapshot: Snapshot, probe: ProfileProbe) -> bool:
    # Any change in count, not only up: a deleted post should leave the cache too.
    if probe.post_count is not None and probe.post_count != snapshot.post_count:
        return True
    return probe.latest_story_at is not None and (
        snapshot.latest_story_at is None or probe.latest_story_at > snapshot.latest_story_at
    )


def drop_expired_stories(page: FeedPage, now: datetime) -> FeedPage:
    posts = [
        post for post in page.posts if not post.is_story or now - post.created_at < STORY_LIFETIME
    ]
    if len(posts) == len(page.posts):
        return page
    has_story = any(post.is_story for post in posts)
    status = page.stories_status
    if status == "available" and not has_story:
        status = "none"
    return page.model_copy(update={"posts": posts, "stories_status": status})


async def first_page(
    platform: str,
    username: str,
    source: FeedSource,
    prober: ProfileProber,
    cache: PageCache,
    *,
    refresh: bool,
) -> FirstPage:
    now = datetime.now(UTC)
    key = page_key("feed", platform, username)
    cached = None if refresh else await cache.get(key, FeedPage)

    if cached is not None:
        snapshot = await cache.get_snapshot(platform, username)
        if snapshot is not None and now - snapshot.checked_at < CHECK_INTERVAL:
            return _from_cache(cached, snapshot.checked_at, now)
        try:
            probe = await prober.probe(username)
        except SourceError as exc:
            # Rate limited or the platform is down: the cached page is the right answer.
            logger.info("probe of %s/%s failed, serving cache: %s", platform, username, exc)
            return _from_cache(cached, snapshot.checked_at if snapshot else None, now)
        await cache.save_snapshot(platform, username, probe, now)
        # No snapshot yet (cache older than this feature): this probe becomes the baseline.
        if snapshot is None or not has_changed(snapshot, probe):
            return _from_cache(cached, now, now)
        logger.info("%s/%s changed since last check, scraping again", platform, username)
        try:
            return await _scrape(platform, username, source, prober, cache, now, probe)
        except SourceError as exc:
            logger.info(
                "scrape of changed %s/%s failed, serving cache: %s", platform, username, exc
            )
            return _from_cache(cached, now, now)

    return await _scrape(platform, username, source, prober, cache, now, None)


def _from_cache(
    cached: CachedEntry[FeedPage], checked_at: datetime | None, now: datetime
) -> FirstPage:
    return FirstPage(
        page=drop_expired_stories(cached.value, now),
        from_cache=True,
        fetched_at=cached.fetched_at,
        checked_at=checked_at,
    )


async def _scrape(
    platform: str,
    username: str,
    source: FeedSource,
    prober: ProfileProber,
    cache: PageCache,
    now: datetime,
    probe: ProfileProbe | None,
) -> FirstPage:
    page = await source.fetch_page(username, None)
    # Older pages, reels and highlights of this account may be stale now as well.
    await cache.invalidate_account(platform, username)
    await cache.set(page_key("feed", platform, username), platform, username, page)
    baseline = probe or page.snapshot
    if baseline is None:
        # TikTok pages carry no counts: one cheap probe so the next visit can compare.
        try:
            baseline = await prober.probe(username)
        except SourceError as exc:
            logger.info("baseline probe of %s/%s failed: %s", platform, username, exc)
    if baseline is not None:
        await cache.save_snapshot(platform, username, baseline, now)
    return FirstPage(
        page=page, from_cache=False, fetched_at=now, checked_at=now if baseline else None
    )
