from dataclasses import dataclass
from datetime import UTC, datetime

from app.sources.instagram import map_instagram_post, map_instagram_story_item


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
