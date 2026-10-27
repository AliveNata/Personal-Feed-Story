from typing import Protocol

from app.models import FeedPage


class FeedSource(Protocol):
    async def fetch_page(self, username: str, cursor: str | None) -> FeedPage: ...
