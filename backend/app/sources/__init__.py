from typing import Protocol

from app.models import FeedPage, ProfileProbe

__all__ = ["FeedSource", "ProfileProbe", "ProfileProber"]


class FeedSource(Protocol):
    async def fetch_page(self, username: str, cursor: str | None) -> FeedPage: ...


class ProfileProber(Protocol):
    async def probe(self, username: str) -> ProfileProbe: ...
