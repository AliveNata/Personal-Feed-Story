from datetime import datetime
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

Platform = Literal["instagram", "tiktok"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


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


class FeedResponse(CamelModel):
    platform: Platform
    username: str
    posts: list[Post]
    cached: bool


def proxied_media_url(raw_url: str) -> str:
    """Route CDN media through our backend: IG/TikTok CDNs block hotlinking from other origins."""
    if not raw_url:
        return ""
    return f"/api/media?url={quote(raw_url, safe='')}"
