from typing import Protocol

from app.models import Post


class FeedSource(Protocol):
    async def fetch(self, username: str, limit: int) -> list[Post]: ...
