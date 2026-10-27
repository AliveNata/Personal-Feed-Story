from datetime import datetime
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

Platform = Literal["instagram", "tiktok"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class MediaSlide(CamelModel):
    type: Literal["photo", "video"]
    url: str
    # Cover frame for video slides.
    poster_url: str | None = None


class Post(CamelModel):
    id: str
    image_url: str
    caption: str
    likes: int
    comments: int
    # Instagram does not expose share counts.
    shares: int | None
    created_at: datetime
    is_story: bool
    permalink: str | None = None
    # Every slide of a carousel, or the single photo or video. Empty when unknown.
    media: list[MediaSlide] = []


StoriesStatus = Literal["available", "none", "login_required", "not_supported"]


class FeedPage(CamelModel):
    posts: list[Post]
    next_cursor: str | None
    stories_status: StoriesStatus


class RateInfo(CamelModel):
    used: int
    limit: int
    window_seconds: int
    wait_seconds: int
    near_limit: bool


class FeedResponse(CamelModel):
    platform: Platform
    username: str
    posts: list[Post]
    cached: bool
    next_cursor: str | None = None
    stories_status: StoriesStatus = "none"
    rate: RateInfo | None = None


class Highlight(CamelModel):
    id: str
    title: str
    cover_url: str
    # Uncropped cover, tried by the UI when the cropped one fails to load.
    fallback_cover_url: str | None = None
    item_count: int | None


class HighlightsResponse(CamelModel):
    status: Literal["available", "none", "login_required"]
    highlights: list[Highlight]
    rate: RateInfo | None = None


class HighlightItemsResponse(CamelModel):
    items: list[Post]
    rate: RateInfo | None = None


def proxied_media_url(raw_url: str) -> str:
    """Route CDN media through our backend: IG/TikTok CDNs block hotlinking from other origins."""
    if not raw_url:
        return ""
    return f"/api/media?url={quote(raw_url, safe='')}"
