from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.models import FeedPage


class FeedSource(Protocol):
    async def fetch_page(self, username: str, cursor: str | None) -> FeedPage: ...


@dataclass(frozen=True)
class ProfileProbe:
    """Cheap snapshot of an account, used to spot new activity on favorites."""

    # Raw CDN URL, proxied when sent to the browser. Empty when unknown.
    avatar_url: str
    # Post count is the cheapest "something new" signal on both platforms: it comes with the
    # profile lookup, while newest post dates cost extra requests. A deleted post can hide a
    # new one, which is acceptable for a hint.
    post_count: int | None
    # Newest active story item, None when there is no story or it cannot be checked.
    latest_story_at: datetime | None


class ProfileProber(Protocol):
    async def probe(self, username: str) -> ProfileProbe: ...
