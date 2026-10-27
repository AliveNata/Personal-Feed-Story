from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

MediaType = Literal["photo", "video"]


@dataclass(frozen=True)
class ResolvedPost:
    """A post with its original media URLs, resolved right before download.

    Feed data only carries covers, and CDN links expire, so the worker asks the platform again.
    """

    post_id: str
    author_username: str
    caption: str
    permalink: str | None
    posted_at: datetime | None
    is_story: bool
    media_urls: list[str]
    request_headers: dict[str, str] = field(default_factory=dict)


class MediaResolver(Protocol):
    async def resolve_media(self, username: str, post_id: str) -> ResolvedPost: ...
