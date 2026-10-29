"""Scraped pages kept in Postgres for a month, plus the cheap snapshot used to spot changes.

Postgres instead of Redis: Redis here runs without persistence, so a restart would throw away
a month of pages and every account would be scraped again.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar

from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import AccountSnapshot, CachedPage
from app.sources import ProfileProbe

logger = logging.getLogger(__name__)

PAGE_TTL = timedelta(days=30)

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class CachedEntry[M: BaseModel]:
    value: M
    fetched_at: datetime


@dataclass(frozen=True)
class Snapshot:
    post_count: int | None
    latest_story_at: datetime | None
    checked_at: datetime


def page_key(kind: str, platform: str, username: str, cursor: str | None = None) -> str:
    return f"{kind}:{platform}:{username.lower()}:{cursor or 'first'}"


class PageCache(Protocol):
    async def get(self, key: str, model: type[M]) -> CachedEntry[M] | None: ...
    async def set(self, key: str, platform: str, username: str, value: BaseModel) -> None: ...
    async def invalidate_account(self, platform: str, username: str) -> None: ...
    async def get_snapshot(self, platform: str, username: str) -> Snapshot | None: ...
    async def save_snapshot(
        self, platform: str, username: str, probe: ProfileProbe, checked_at: datetime
    ) -> None: ...


class PgPageCache:
    """Every failure degrades to a cache miss: the feed still works, it just scrapes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, key: str, model: type[M]) -> CachedEntry[M] | None:
        try:
            async with self._session_factory() as db:
                row = await db.get(CachedPage, key)
        except SQLAlchemyError as exc:
            logger.warning("page cache read failed for %s: %s", key, exc)
            return None
        if row is None or row.expires_at <= datetime.now(UTC):
            return None
        return CachedEntry(model.model_validate(row.payload), row.fetched_at)

    async def set(self, key: str, platform: str, username: str, value: BaseModel) -> None:
        now = datetime.now(UTC)
        values = {
            "key": key,
            "platform": platform,
            "username": username.lower(),
            "payload": value.model_dump(mode="json"),
            "fetched_at": now,
            "expires_at": now + PAGE_TTL,
        }
        statement = insert(CachedPage).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[CachedPage.key],
            set_={name: statement.excluded[name] for name in values if name != "key"},
        )
        try:
            async with self._session_factory() as db:
                await db.execute(statement)
                await db.commit()
        except SQLAlchemyError as exc:
            logger.warning("page cache write failed for %s: %s", key, exc)

    async def invalidate_account(self, platform: str, username: str) -> None:
        try:
            async with self._session_factory() as db:
                await db.execute(
                    delete(CachedPage).where(
                        CachedPage.platform == platform, CachedPage.username == username.lower()
                    )
                )
                await db.commit()
        except SQLAlchemyError as exc:
            logger.warning("page cache invalidate failed for %s/%s: %s", platform, username, exc)

    async def get_snapshot(self, platform: str, username: str) -> Snapshot | None:
        try:
            async with self._session_factory() as db:
                row = await db.get(AccountSnapshot, (platform, username.lower()))
        except SQLAlchemyError as exc:
            logger.warning("snapshot read failed for %s/%s: %s", platform, username, exc)
            return None
        if row is None:
            return None
        return Snapshot(row.post_count, row.latest_story_at, row.checked_at)

    async def save_snapshot(
        self, platform: str, username: str, probe: ProfileProbe, checked_at: datetime
    ) -> None:
        values = {
            "platform": platform,
            "username": username.lower(),
            "post_count": probe.post_count,
            "latest_story_at": probe.latest_story_at,
            "checked_at": checked_at,
        }
        statement = insert(AccountSnapshot).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[AccountSnapshot.platform, AccountSnapshot.username],
            set_={
                name: statement.excluded[name]
                for name in ("post_count", "latest_story_at", "checked_at")
            },
        )
        try:
            async with self._session_factory() as db:
                await db.execute(statement)
                await db.commit()
        except SQLAlchemyError as exc:
            logger.warning("snapshot write failed for %s/%s: %s", platform, username, exc)


async def purge_expired(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as db:
        result = await db.execute(
            delete(CachedPage).where(CachedPage.expires_at <= datetime.now(UTC))
        )
        await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
