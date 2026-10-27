from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from app.errors import UserNotFoundError
from app.main import app, get_budget, get_cache, get_media_session, get_sources
from app.media import is_allowed_media_url
from app.models import FeedPage, Post


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

    async def fetch_page(self, username: str, cursor: str | None) -> FeedPage:
        self.calls.append((username, cursor))
        if self.error is not None:
            raise self.error
        return FeedPage(posts=self.posts, next_cursor="next1", stories_status="none")


class FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, FeedPage] = {}

    async def get(self, key: str) -> FeedPage | None:
        return self.store.get(key)

    async def set(self, key: str, page: FeedPage) -> None:
        self.store[key] = page


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
