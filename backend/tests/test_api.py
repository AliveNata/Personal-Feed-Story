from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import BaseModel

from app.cache import CachedEntry, M, Snapshot
from app.errors import UserNotFoundError
from app.main import app, get_budget, get_cache, get_media_session, get_sources
from app.media import is_allowed_media_url
from app.models import FeedPage, Post, ProfileProbe


def sample_post(post_id: str = "1") -> Post:
    return Post(
        id=post_id,
        image_url="/api/media?url=x",
        caption="c",
        likes=1,
        comments=2,
        shares=3,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        is_story=False,
    )


class FakeSource:
    def __init__(self, posts: list[Post] | None = None, error: Exception | None = None) -> None:
        self.posts = posts or []
        self.error = error
        self.calls: list[tuple[str, str | None]] = []
        self.post_count = 10
        self.probes = 0

    async def probe(self, username: str) -> ProfileProbe:
        self.probes += 1
        return ProfileProbe(avatar_url="", post_count=self.post_count, latest_story_at=None)

    async def fetch_page(self, username: str, cursor: str | None) -> FeedPage:
        self.calls.append((username, cursor))
        if self.error is not None:
            raise self.error
        return FeedPage(posts=self.posts, next_cursor="next1", stories_status="none")


class FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, BaseModel]] = {}
        self.snapshots: dict[tuple[str, str], Snapshot] = {}

    async def get(self, key: str, model: type[M]) -> CachedEntry[M] | None:
        entry = self.store.get(key)
        if entry is None:
            return None
        return CachedEntry(model.model_validate(entry[1].model_dump()), datetime.now(UTC))

    async def set(self, key: str, platform: str, username: str, value: BaseModel) -> None:
        self.store[key] = (f"{platform}:{username.lower()}", value)

    async def invalidate_account(self, platform: str, username: str) -> None:
        account = f"{platform}:{username.lower()}"
        self.store = {k: v for k, v in self.store.items() if v[0] != account}

    async def get_snapshot(self, platform: str, username: str) -> Snapshot | None:
        return self.snapshots.get((platform, username.lower()))

    async def save_snapshot(
        self, platform: str, username: str, probe: ProfileProbe, checked_at: datetime
    ) -> None:
        self.snapshots[(platform, username.lower())] = Snapshot(
            probe.post_count, probe.latest_story_at, checked_at
        )


@pytest.fixture
def tiktok() -> FakeSource:
    return FakeSource(posts=[sample_post()])


@pytest.fixture
def cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
async def client(tiktok: FakeSource, cache: FakeCache) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[get_sources] = lambda: {"tiktok": tiktok, "instagram": FakeSource()}
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_media_session] = lambda: None
    app.dependency_overrides[get_budget] = lambda: None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()


async def test_feed_returns_camel_case_posts(client: httpx.AsyncClient) -> None:
    # Arrange / Act
    response = await client.get("/api/feed", params={"platform": "tiktok", "username": "@someone"})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "someone"
    assert body["cached"] is False
    assert body["posts"][0]["imageUrl"] == "/api/media?url=x"
    assert body["posts"][0]["isStory"] is False
    assert body["nextCursor"] == "next1"
    assert body["storiesStatus"] == "none"


async def test_feed_serves_second_call_from_cache(
    client: httpx.AsyncClient, tiktok: FakeSource
) -> None:
    # Arrange
    params = {"platform": "tiktok", "username": "someone"}
    await client.get("/api/feed", params=params)

    # Act
    response = await client.get("/api/feed", params=params)

    # Assert
    assert response.json()["cached"] is True
    assert len(tiktok.calls) == 1


async def test_feed_refresh_bypasses_cache(client: httpx.AsyncClient, tiktok: FakeSource) -> None:
    # Arrange
    params = {"platform": "tiktok", "username": "someone"}
    await client.get("/api/feed", params=params)

    # Act
    await client.get("/api/feed", params={**params, "refresh": "true"})

    # Assert
    assert len(tiktok.calls) == 2


async def test_feed_maps_source_error_to_status(
    client: httpx.AsyncClient, tiktok: FakeSource
) -> None:
    # Arrange
    tiktok.error = UserNotFoundError("Akun tidak ditemukan")

    # Act
    response = await client.get("/api/feed", params={"platform": "tiktok", "username": "ghost"})

    # Assert
    assert response.status_code == 404
    assert response.json()["detail"] == "Akun tidak ditemukan"


async def test_feed_rejects_invalid_username(client: httpx.AsyncClient) -> None:
    # Arrange / Act
    response = await client.get(
        "/api/feed", params={"platform": "tiktok", "username": "../etc/passwd"}
    )

    # Assert
    assert response.status_code == 422


async def test_media_rejects_non_cdn_host(client: httpx.AsyncClient) -> None:
    # Arrange / Act
    response = await client.get("/api/media", params={"url": "http://169.254.169.254/latest"})

    # Assert
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://scontent-sin6-1.cdninstagram.com/v/a.jpg", True),
        ("https://p16-sign-sg.tiktokcdn.com/obj/a.jpeg", True),
        ("http://p16-sign-sg.tiktokcdn.com/obj/a.jpeg", False),
        ("https://evil.com/?x=.tiktokcdn.com", False),
        ("https://tiktokcdn.com.evil.com/a.jpg", False),
        ("https://v16-webapp-prime.tiktok.com/video/tos/a", True),
        ("https://www.tiktok.com/@someone", False),
    ],
)
def test_is_allowed_media_url(url: str, expected: bool) -> None:
    # Arrange / Act
    result = is_allowed_media_url(url)

    # Assert
    assert result is expected


async def test_feed_passes_cursor_and_caches_per_page(
    client: httpx.AsyncClient, tiktok: FakeSource
) -> None:
    # Arrange
    params = {"platform": "tiktok", "username": "someone"}
    await client.get("/api/feed", params=params)

    # Act
    response = await client.get("/api/feed", params={**params, "cursor": "1"})

    # Assert
    assert response.json()["cached"] is False
    assert tiktok.calls == [("someone", None), ("someone", "1")]


async def test_rate_limit_error_carries_retry_after(
    client: httpx.AsyncClient, tiktok: FakeSource
) -> None:
    # Arrange
    from app.errors import RateLimitedError

    tiktok.error = RateLimitedError("tunggu 5 menit", retry_after=300)

    # Act
    response = await client.get("/api/feed", params={"platform": "tiktok", "username": "x"})

    # Assert
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    assert response.json() == {"detail": "tunggu 5 menit", "retryAfter": 300}


async def test_cached_feed_is_checked_cheaply_and_rescraped_only_on_change(
    client: httpx.AsyncClient, tiktok: FakeSource, cache: FakeCache
) -> None:
    # Arrange: first visit scrapes and stores a baseline
    params = {"platform": "tiktok", "username": "someone"}
    await client.get("/api/feed", params=params)
    snapshot = cache.snapshots[("tiktok", "someone")]
    stale = Snapshot(snapshot.post_count, None, snapshot.checked_at - timedelta(hours=2))

    # Act: check is due but nothing changed, then a new post appears
    cache.snapshots[("tiktok", "someone")] = stale
    unchanged = (await client.get("/api/feed", params=params)).json()
    cache.snapshots[("tiktok", "someone")] = stale
    tiktok.post_count = 11
    changed = (await client.get("/api/feed", params=params)).json()

    # Assert
    assert unchanged["cached"] is True
    assert changed["cached"] is False
    assert len(tiktok.calls) == 2
