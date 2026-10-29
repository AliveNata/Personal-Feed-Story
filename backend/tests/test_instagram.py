from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

import fakeredis
import instaloader
import pytest
from instaloader.exceptions import (
    AbortDownloadException,
    ConnectionException,
    ProfileNotExistsException,
)

from app.errors import RateLimitedError
from app.platform_sessions import InvalidSessionError
from app.ratelimit import RateBudget
from app.sources.instagram import (
    CachedMedia,
    InstagramSource,
    MediaCache,
    _has_more,
    _to_highlight,
    instagram_media_urls,
    instagram_slides,
    map_instagram_post,
    map_instagram_story_item,
    validate_instagram_cookie,
)


@dataclass
class FakePost:
    shortcode: str
    url: str
    caption: str | None
    likes: int
    comments: int
    date_utc: datetime


@dataclass
class FakeStoryItem:
    mediaid: int
    url: str
    caption: str | None
    date_utc: datetime
    is_video: bool = False
    video_url: str | None = None


@dataclass
class FakeNode:
    is_video: bool
    display_url: str
    video_url: str | None = None


@dataclass
class FakeFullPost:
    typename: str
    is_video: bool
    url: str
    video_url: str | None = None
    nodes: list[FakeNode] = field(default_factory=list)

    def get_sidecar_nodes(self) -> list[FakeNode]:
        return self.nodes


def test_map_instagram_post_sets_utc_and_permalink() -> None:
    # Arrange
    post = FakePost(
        shortcode="Cabc123",
        url="https://scontent.cdninstagram.com/v/p.jpg",
        caption=None,
        likes=-1,
        comments=3,
        date_utc=datetime(2026, 10, 1, 12, 0),
    )

    # Act
    result = map_instagram_post(post)

    # Assert
    assert result.id == "Cabc123"
    assert result.caption == ""
    assert result.likes == 0
    assert result.shares is None
    assert result.created_at == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert result.permalink == "https://www.instagram.com/p/Cabc123/"
    assert result.image_url.startswith("/api/media?url=")


def test_map_instagram_story_item_marks_story() -> None:
    # Arrange
    item = FakeStoryItem(
        mediaid=42,
        url="https://scontent.cdninstagram.com/v/s.jpg",
        caption="hi",
        date_utc=datetime(2026, 10, 27, 8, 0),
    )

    # Act
    result = map_instagram_story_item(item)

    # Assert
    assert result.id == "story-42"
    assert result.is_story is True
    assert result.caption == "hi"


def test_instagram_slides_cover_every_carousel_node() -> None:
    # Arrange
    post = FakeFullPost(
        typename="GraphSidecar",
        is_video=False,
        url="https://scontent.cdninstagram.com/cover.jpg",
        nodes=[
            FakeNode(False, "https://scontent.cdninstagram.com/1.jpg"),
            FakeNode(
                True,
                "https://scontent.cdninstagram.com/2.jpg",
                "https://scontent.cdninstagram.com/2.mp4",
            ),
        ],
    )

    # Act
    slides = instagram_slides(post)
    urls = instagram_media_urls(post)

    # Assert
    assert [slide.type for slide in slides] == ["photo", "video"]
    assert slides[1].poster_url is not None and "2.jpg" in slides[1].poster_url
    assert urls == [
        "https://scontent.cdninstagram.com/1.jpg",
        "https://scontent.cdninstagram.com/2.mp4",
    ]


def test_story_video_item_uses_video_file_with_poster() -> None:
    # Arrange
    item = FakeStoryItem(
        mediaid=7,
        url="https://scontent.cdninstagram.com/v/s.jpg",
        caption=None,
        date_utc=datetime(2026, 10, 27, 8, 0),
        is_video=True,
        video_url="https://scontent.cdninstagram.com/v/s.mp4",
    )

    # Act
    result = map_instagram_story_item(item)

    # Assert
    assert result.media[0].type == "video"
    assert "s.mp4" in result.media[0].url


@dataclass
class FakeHighlight:
    unique_id: int
    title: str
    cover_url: str | None
    cover_cropped_url: str | None


def test_highlight_prefers_full_cover_over_cropped() -> None:
    # Arrange
    item = FakeHighlight(1, "me", "https://cdn.test/full.jpg", "https://cdn.test/crop.jpg")

    # Act
    highlight = _to_highlight(item)

    # Assert
    assert "full.jpg" in highlight.cover_url
    assert highlight.fallback_cover_url is not None
    assert "crop.jpg" in highlight.fallback_cover_url


def test_highlight_uses_cropped_cover_when_full_missing() -> None:
    # Arrange
    item = FakeHighlight(2, "job", None, "https://cdn.test/crop.jpg")

    # Act
    highlight = _to_highlight(item)

    # Assert
    assert "crop.jpg" in highlight.cover_url
    assert highlight.fallback_cover_url is None


def make_source(redis: fakeredis.FakeRedis) -> InstagramSource:
    loader = SimpleNamespace(context=SimpleNamespace(is_logged_in=True))
    return InstagramSource(loader, True, media_cache=MediaCache(redis))  # type: ignore[arg-type]


async def test_resolve_media_uses_urls_cached_from_the_feed() -> None:
    # Arrange
    redis = fakeredis.FakeRedis()
    source = make_source(redis)
    story = FakeStoryItem(
        mediaid=42,
        url="https://scontent.cdninstagram.com/cover.jpg",
        caption="halo",
        date_utc=datetime(2026, 10, 29, 1, 0),
        is_video=True,
        video_url="https://scontent.cdninstagram.com/clip.mp4",
    )
    source._cache_story_items([story])

    # Act
    resolved = await source.resolve_media("someone", "story-42")

    # Assert
    assert resolved.from_cache is True
    assert resolved.is_story is True
    assert resolved.media_urls == ["https://scontent.cdninstagram.com/clip.mp4"]
    assert resolved.posted_at == datetime(2026, 10, 29, 1, 0, tzinfo=UTC)


async def test_fresh_resolve_drops_the_cached_entry() -> None:
    # Arrange
    redis = fakeredis.FakeRedis()
    cache = MediaCache(redis)
    cache.put_many(
        {
            "ABC": CachedMedia(
                media_urls=["https://scontent.cdninstagram.com/a.jpg"],
                caption="",
                permalink=None,
                posted_at=datetime(2026, 10, 29, tzinfo=UTC),
                is_story=False,
            )
        }
    )

    # Act
    cache.forget("ABC")

    # Assert
    assert cache.get("ABC") is None


def test_feedback_required_becomes_rate_limit() -> None:
    # Arrange
    source = make_source(fakeredis.FakeRedis())

    def blocked() -> None:
        raise AbortDownloadException('400 Bad Request - "fail" status, message "feedback_required"')

    # Act / Assert
    with pytest.raises(RateLimitedError) as caught:
        source._guarded(blocked)
    assert caught.value.retry_after == 30 * 60


async def test_reels_pause_when_budget_share_is_used() -> None:
    # Arrange
    redis = fakeredis.FakeRedis()
    budget = RateBudget(redis, {"instagram": (100, 900)})
    budget.record("instagram", 55)
    loader = SimpleNamespace(context=SimpleNamespace(is_logged_in=True))
    source = InstagramSource(loader, True, budget=budget)  # type: ignore[arg-type]

    # Act / Assert
    with pytest.raises(RateLimitedError) as caught:
        await source.fetch_reels("someone", None)
    assert "Reels dijeda" in str(caught.value)
    assert caught.value.retry_after


async def test_reels_need_a_session() -> None:
    # Arrange
    loader = SimpleNamespace(context=SimpleNamespace(is_logged_in=False))
    source = InstagramSource(loader, True)  # type: ignore[arg-type]

    # Act
    status, reels, cursor = await source.fetch_reels("someone", None)

    # Assert
    assert (status, reels, cursor) == ("login_required", [], None)


def test_has_more_reads_page_state_without_pulling_items() -> None:
    # Arrange
    last_page = SimpleNamespace(
        _page_index=2, _data={"edges": [{}, {}], "page_info": {"has_next_page": False}}
    )
    mid_page = SimpleNamespace(
        _page_index=1, _data={"edges": [{}, {}], "page_info": {"has_next_page": False}}
    )

    # Act / Assert
    assert _has_more(last_page) is False
    assert _has_more(mid_page) is True
    assert _has_more(object()) is True


def test_profile_falls_back_to_profile_api_when_page_has_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    def page_without_data(context: object, username: str) -> None:
        raise ProfileNotExistsException(f"Profile {username} does not exist.")

    monkeypatch.setattr(instaloader.Profile, "from_username", page_without_data)
    api_user = {"id": "123", "username": "linuarinuja_new", "edge_owner_to_timeline_media": {}}
    context = SimpleNamespace(
        is_logged_in=True, get_json=lambda path, params: {"data": {"user": api_user}}
    )
    source = InstagramSource(SimpleNamespace(context=context), True)  # type: ignore[arg-type]

    # Act
    profile = source._profile("linuarinuja_new")

    # Assert
    assert profile.userid == 123


def test_profile_still_missing_when_api_has_no_user(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    def page_without_data(context: object, username: str) -> None:
        raise ProfileNotExistsException("nope")

    monkeypatch.setattr(instaloader.Profile, "from_username", page_without_data)
    context = SimpleNamespace(
        is_logged_in=True, get_json=lambda path, params: {"data": {"user": None}}
    )
    source = InstagramSource(SimpleNamespace(context=context), True)  # type: ignore[arg-type]

    # Act / Assert
    with pytest.raises(ProfileNotExistsException):
        source._profile("ghost")


def test_profile_api_429_is_a_rate_limit_not_a_missing_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    def page_without_data(context: object, username: str) -> None:
        raise ProfileNotExistsException("nope")

    def limited(path: str, params: dict[str, str]) -> None:
        raise ConnectionException(f"JSON Query to {path}: 429 Too Many Requests")

    monkeypatch.setattr(instaloader.Profile, "from_username", page_without_data)
    context = SimpleNamespace(is_logged_in=True, get_json=limited)
    budget = RateBudget(fakeredis.FakeRedis(), {"instagram": (100, 900)})
    source = InstagramSource(SimpleNamespace(context=context), True, budget=budget)  # type: ignore[arg-type]

    # Act / Assert
    with pytest.raises(RateLimitedError):
        source._guarded(lambda: source._profile("linuarinuja_new"))
    assert budget.status("instagram").wait_seconds > 0


def test_401_please_wait_is_a_rate_limit_with_cooldown() -> None:
    # Arrange
    budget = RateBudget(fakeredis.FakeRedis(), {"instagram": (100, 900)})
    loader = SimpleNamespace(context=SimpleNamespace(is_logged_in=True))
    source = InstagramSource(loader, True, budget=budget)  # type: ignore[arg-type]

    def throttled() -> None:
        raise ConnectionException(
            'JSON Query to graphql/query: 401 Unauthorized - "fail" status, message '
            '"Please wait a few minutes before you try again."'
        )

    # Act / Assert
    with pytest.raises(RateLimitedError):
        source._guarded(throttled)
    assert budget.status("instagram").wait_seconds > 0


def test_pasted_instagram_cookie_needs_a_sessionid() -> None:
    # Arrange / Act / Assert
    with pytest.raises(InvalidSessionError):
        validate_instagram_cookie("csrftoken=abc; mid=xyz")
