from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.sources.instagram import (
    _to_highlight,
    instagram_media_urls,
    instagram_slides,
    map_instagram_post,
    map_instagram_story_item,
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
