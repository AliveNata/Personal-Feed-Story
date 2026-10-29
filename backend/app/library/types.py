from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

MediaType = Literal["photo", "video"]


@dataclass(frozen=True)
class ResolvedPost:
    """A post with its original media URLs, resolved right before download.

    Comes from the platform, or for Instagram from the URLs cached when the feed was shown
    (from_cache). Cached CDN links can expire, so a failed download retries with fresh=True.
    """

    post_id: str
    author_username: str
    caption: str
    permalink: str | None
    posted_at: datetime | None
    is_story: bool
    media_urls: list[str]
    request_headers: dict[str, str] = field(default_factory=dict)
    from_cache: bool = False


class MediaResolver(Protocol):
    async def resolve_media(
        self, username: str, post_id: str, *, fresh: bool = False
    ) -> ResolvedPost: ...
