from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from app.errors import PrivateAccountError, UpstreamError, UserNotFoundError
from app.sources.tiktok import TikTokSource, map_douk_item

DOUK_URL = "http://douk:5555"
SEC_UID = "MS4wLjABAAAAabc_DEF-123"


def douk_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": "7300000000000000001",
        "desc": "hello #fyp",
        "create_timestamp": 1_761_500_000,
        "digg_count": 120,
        "comment_count": 4,
        "share_count": 2,
        "static_cover": "https://p16-sign.tiktokcdn.com/cover.jpeg",
        "downloads": "https://v16.tiktokcdn.com/video.mp4",
        "share_url": "https://www.tiktok.com/@someone/video/7300000000000000001",
    }
    item.update(overrides)
    return item


def make_source(client: httpx.AsyncClient) -> TikTokSource:
    return TikTokSource(client, DOUK_URL, cookie="sid=x", lookback_days=30)


def test_map_douk_item_maps_video_fields() -> None:
    # Arrange
    item = douk_item()

    # Act
    post = map_douk_item(item)

    # Assert
    assert post.id == "7300000000000000001"
    assert post.caption == "hello #fyp"
    assert (post.likes, post.comments, post.shares) == (120, 4, 2)
    assert post.created_at == datetime.fromtimestamp(1_761_500_000, tz=UTC)
    assert post.image_url.startswith("/api/media?url=https%3A%2F%2Fp16-sign.tiktokcdn.com")
    assert post.is_story is False
    assert [(slide.type, slide.poster_url is not None) for slide in post.media] == [("video", True)]


def test_map_douk_item_uses_first_slide_when_no_cover() -> None:
    # Arrange
    item = douk_item(
        static_cover="",
        downloads=["https://p16.tiktokcdn.com/slide1.jpeg", "https://p16.tiktokcdn.com/2.jpeg"],
    )

    # Act
    post = map_douk_item(item)

    # Assert
    assert "slide1.jpeg" in post.image_url
    assert [slide.type for slide in post.media] == ["photo", "photo"]


def test_map_douk_item_clamps_missing_stats_to_zero() -> None:
    # Arrange
    item = douk_item(digg_count=-1, comment_count=None, share_count="x")

    # Act
    post = map_douk_item(item)

    # Assert
    assert (post.likes, post.comments, post.shares) == (0, 0, 0)


async def test_resolve_sec_uid_passes_through_existing_sec_uid() -> None:
    # Arrange
    async with httpx.AsyncClient() as client:
        source = make_source(client)

        # Act
        result = await source.resolve_sec_uid(SEC_UID)

    # Assert
    assert result == SEC_UID


@respx.mock
async def test_resolve_sec_uid_uses_douk_user_route_and_caches() -> None:
    # Arrange
    route = respx.post(f"{DOUK_URL}/tiktok/user").respond(
        200, json={"message": "ok", "data": {"user": {"secUid": SEC_UID}}, "params": {}}
    )

    async with httpx.AsyncClient() as client:
        source = make_source(client)

        # Act
        first = await source.resolve_sec_uid("Someone")
        second = await source.resolve_sec_uid("someone")

    # Assert
    assert first == second == SEC_UID
    assert route.call_count == 1
    body = httpx.Response(200, content=route.calls.last.request.content).json()
    assert body == {"unique_id": "Someone", "cookie": "sid=x"}


@respx.mock
async def test_resolve_sec_uid_raises_not_found_when_douk_has_no_user() -> None:
    # Arrange
    respx.post(f"{DOUK_URL}/tiktok/user").respond(
        200, json={"message": "failed", "data": None, "params": {}}
    )

    async with httpx.AsyncClient() as client:
        # Act / Assert
        with pytest.raises(UserNotFoundError):
            await make_source(client).resolve_sec_uid("ghost")


@respx.mock
async def test_fetch_page_requests_one_window_and_sorts_newest_first() -> None:
    # Arrange
    older = douk_item(id="1", create_timestamp=1_700_000_000)
    newer = douk_item(id="2", create_timestamp=1_760_000_000)
    route = respx.post(f"{DOUK_URL}/tiktok/account").respond(
        200, json={"message": "ok", "data": [older, newer], "params": {}}
    )

    async with httpx.AsyncClient() as client:
        # Act
        page = await make_source(client).fetch_page(SEC_UID, "2")

    # Assert
    sent = route.calls.last.request
    body = httpx.Response(200, content=sent.content).json()
    assert body["sec_user_id"] == SEC_UID
    assert (body["earliest"], body["latest"]) == (30, 60)
    assert body["cookie"] == "sid=x"
    assert [post.id for post in page.posts] == ["2", "1"]
    assert page.next_cursor == "3"
    assert page.stories_status == "not_supported"


@respx.mock
async def test_fetch_page_beyond_first_window_returns_empty_page_on_no_data() -> None:
    # Arrange
    respx.post(f"{DOUK_URL}/tiktok/account").respond(
        200, json={"message": "failed", "data": None, "params": {}}
    )

    async with httpx.AsyncClient() as client:
        # Act
        page = await make_source(client).fetch_page(SEC_UID, "12")

    # Assert
    assert page.posts == []
    assert page.next_cursor is None


@respx.mock
async def test_fetch_raises_private_when_douk_returns_no_data() -> None:
    # Arrange
    respx.post(f"{DOUK_URL}/tiktok/account").respond(
        200, json={"message": "failed", "data": None, "params": {}}
    )

    async with httpx.AsyncClient() as client:
        # Act / Assert
        with pytest.raises(PrivateAccountError):
            await make_source(client).fetch_page(SEC_UID, None)


@respx.mock
async def test_fetch_raises_upstream_when_douk_is_down() -> None:
    # Arrange
    respx.post(f"{DOUK_URL}/tiktok/account").mock(side_effect=httpx.ConnectError("refused"))

    async with httpx.AsyncClient() as client:
        # Act / Assert
        with pytest.raises(UpstreamError):
            await make_source(client).fetch_page(SEC_UID, None)
