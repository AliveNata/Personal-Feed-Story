import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import freshness
from app.cache import CachedEntry, M, PgPageCache, Snapshot, page_key, purge_expired
from app.db import CachedPage
from app.errors import RateLimitedError
from app.mediacache import MediaFileCache
from app.models import FeedPage, Post, ProfileProbe
from tests.conftest import requires_db

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def post(post_id: str, *, story: bool = False, age: timedelta = timedelta()) -> Post:
    return Post(
        id=post_id,
        image_url="",
        caption="",
        likes=0,
        comments=0,
        shares=None,
        created_at=NOW - age,
        is_story=story,
    )


def probe(count: int | None, story_at: datetime | None = None) -> ProfileProbe:
    return ProfileProbe(avatar_url="", post_count=count, latest_story_at=story_at)


def test_count_change_either_way_and_newer_story_mean_changed() -> None:
    # Arrange
    snapshot = Snapshot(post_count=10, latest_story_at=NOW, checked_at=NOW)

    # Act / Assert
    assert freshness.has_changed(snapshot, probe(11)) is True
    assert freshness.has_changed(snapshot, probe(9)) is True
    assert freshness.has_changed(snapshot, probe(10, NOW + timedelta(minutes=1))) is True
    assert freshness.has_changed(snapshot, probe(10, NOW)) is False
    assert freshness.has_changed(snapshot, probe(None)) is False


def test_stories_older_than_a_day_are_hidden_from_cached_pages() -> None:
    # Arrange
    page = FeedPage(
        posts=[
            post("story-1", story=True, age=timedelta(hours=25)),
            post("P1", age=timedelta(days=9)),
        ],
        next_cursor=None,
        stories_status="available",
    )

    # Act
    shown = freshness.drop_expired_stories(page, NOW)

    # Assert
    assert [item.id for item in shown.posts] == ["P1"]
    assert shown.stories_status == "none"


class MemoryCache:
    """In memory PageCache with a fixed clock for the cached entry."""

    def __init__(self) -> None:
        self.pages: dict[str, BaseModel] = {}
        self.snapshot: Snapshot | None = None

    async def get(self, key: str, model: type[M]) -> CachedEntry[M] | None:
        page = self.pages.get(key)
        if page is None:
            return None
        return CachedEntry(model.model_validate(page.model_dump()), NOW - timedelta(days=3))

    async def set(self, key: str, platform: str, username: str, value: BaseModel) -> None:
        self.pages[key] = value

    async def invalidate_account(self, platform: str, username: str) -> None:
        self.pages.clear()

    async def get_snapshot(self, platform: str, username: str) -> Snapshot | None:
        return self.snapshot

    async def save_snapshot(
        self, platform: str, username: str, probe: ProfileProbe, checked_at: datetime
    ) -> None:
        self.snapshot = Snapshot(probe.post_count, probe.latest_story_at, checked_at)


class FlakySource:
    def __init__(self, probe_error: Exception | None = None, scrape_error: Exception | None = None):
        self.probe_error = probe_error
        self.scrape_error = scrape_error
        self.scrapes = 0

    async def probe(self, username: str) -> ProfileProbe:
        if self.probe_error:
            raise self.probe_error
        return probe(12)

    async def fetch_page(self, username: str, cursor: str | None) -> FeedPage:
        self.scrapes += 1
        if self.scrape_error:
            raise self.scrape_error
        return FeedPage(posts=[post("NEW")], next_cursor=None, stories_status="none")


def cached_setup() -> MemoryCache:
    cache = MemoryCache()
    cache.pages[page_key("feed", "instagram", "someone")] = FeedPage(
        posts=[post("OLD")], next_cursor=None, stories_status="none"
    )
    cache.snapshot = Snapshot(10, None, datetime.now(UTC) - timedelta(hours=2))
    return cache


async def test_rate_limited_probe_serves_the_cached_page() -> None:
    # Arrange
    cache = cached_setup()
    source = FlakySource(probe_error=RateLimitedError("tunggu", retry_after=60))

    # Act
    result = await freshness.first_page(
        "instagram",
        "someone",
        source,
        source,
        cache,
        refresh=False,
    )

    # Assert
    assert (result.from_cache, [p.id for p in result.page.posts], source.scrapes) == (
        True,
        ["OLD"],
        0,
    )


async def test_changed_account_that_cannot_be_scraped_still_shows_cache() -> None:
    # Arrange
    cache = cached_setup()
    source = FlakySource(scrape_error=RateLimitedError("tunggu", retry_after=60))

    # Act
    result = await freshness.first_page(
        "instagram",
        "someone",
        source,
        source,
        cache,
        refresh=False,
    )

    # Assert
    assert result.from_cache is True
    assert [p.id for p in result.page.posts] == ["OLD"]


async def test_recent_check_skips_the_probe() -> None:
    # Arrange
    cache = cached_setup()
    cache.snapshot = Snapshot(10, None, datetime.now(UTC) - timedelta(minutes=10))
    source = FlakySource(probe_error=AssertionError("must not probe"))

    # Act
    result = await freshness.first_page(
        "instagram",
        "someone",
        source,
        source,
        cache,
        refresh=False,
    )

    # Assert
    assert result.from_cache is True


@requires_db
async def test_pg_page_cache_roundtrip_expiry_and_invalidate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    cache = PgPageCache(session_factory)
    page = FeedPage(posts=[post("A")], next_cursor="c", stories_status="none")
    first, second = (
        page_key("feed", "tiktok", "Someone"),
        page_key("feed", "tiktok", "Someone", "1"),
    )

    # Act
    await cache.set(first, "tiktok", "Someone", page)
    await cache.set(first, "tiktok", "Someone", page)  # upsert, no duplicate key error
    await cache.set(second, "tiktok", "someone", page)
    hit = await cache.get(first, FeedPage)
    async with session_factory() as db:
        row = await db.get(CachedPage, second)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    expired = await cache.get(second, FeedPage)
    purged = await purge_expired(session_factory)
    await cache.invalidate_account("tiktok", "SOMEONE")
    gone = await cache.get(first, FeedPage)

    # Assert
    assert hit is not None and hit.value.posts[0].id == "A"
    assert (expired, purged, gone) == (None, 1, None)


@requires_db
async def test_pg_snapshot_upsert(session_factory: async_sessionmaker[AsyncSession]) -> None:
    # Arrange
    cache = PgPageCache(session_factory)

    # Act
    await cache.save_snapshot("instagram", "Someone", probe(5), NOW)
    await cache.save_snapshot("instagram", "someone", probe(6, NOW), NOW + timedelta(hours=1))
    snapshot = await cache.get_snapshot("instagram", "SOMEONE")

    # Assert
    assert snapshot == Snapshot(6, NOW, NOW + timedelta(hours=1))


def test_media_file_cache_roundtrip_and_purge(tmp_path: Path) -> None:
    # Arrange
    cache = MediaFileCache(tmp_path)
    cache.put("https://cdn.test/a.jpg?oe=1", b"jpeg-bytes", "image/jpeg")

    # Act
    hit = cache.get("https://cdn.test/a.jpg?oe=1")
    miss = cache.get("https://cdn.test/a.jpg?oe=2")
    assert hit is not None
    hit_bytes = hit[0].read_bytes()
    old = time.time() - 40 * 24 * 60 * 60
    for path in tmp_path.glob("*/*"):
        os.utime(path, (old, old))
    removed = cache.purge()

    # Assert
    assert hit_bytes == b"jpeg-bytes" and hit[1] == "image/jpeg"
    assert miss is None
    assert removed == 2
    assert cache.get("https://cdn.test/a.jpg?oe=1") is None
