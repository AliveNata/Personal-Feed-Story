"""Platform cookies pasted on the Session page, encrypted at rest and picked up without restart.

The API process and the worker each hold their own Instagram loader and TikTok cookie. They
look at the stored session at most every SYNC_INTERVAL_SECONDS and swap it in when it changed,
so a cookie saved on the web reaches downloads too.
"""

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import PlatformSession

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 10
KEY_FILENAME = "session.key"


class InvalidSessionError(Exception):
    """The pasted cookie cannot be used. The message is shown to the user."""


def parse_cookie_header(raw: str) -> dict[str, str]:
    """'a=1; b=2' into {'a': '1', 'b': '2'}. Tolerates a leading 'Cookie:' and line breaks."""
    text = " ".join(raw.split())
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:") :]
    cookies: dict[str, str] = {}
    for part in text.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies[name.strip()] = value.strip()
    return cookies


class SecretBox:
    """Fernet with a key file next to the other secrets, created on first use.

    The key is kept out of Postgres on purpose: a database dump alone does not reveal cookies.
    """

    def __init__(self, secrets_dir: Path) -> None:
        self._path = secrets_dir / KEY_FILENAME
        self._fernet: Fernet | None = None

    def _cipher(self) -> Fernet:
        if self._fernet is None:
            if not self._path.exists():
                self._path.parent.mkdir(parents=True, exist_ok=True)
                key = Fernet.generate_key()
                # Exclusive create: if the worker raced us, use the key it wrote.
                try:
                    fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as file:
                        file.write(key)
                    logger.info("created session encryption key at %s", self._path)
                except FileExistsError:
                    pass
            self._fernet = Fernet(self._path.read_bytes().strip())
        return self._fernet

    def encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._cipher().decrypt(token.encode()).decode()
        except InvalidToken:
            logger.error("stored platform session cannot be decrypted, was the key file replaced?")
            return None


@dataclass(frozen=True)
class StoredSession:
    platform: str
    cookie: str
    account_name: str
    verified: bool
    updated_at: datetime


class SessionStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], box: SecretBox) -> None:
        self._session_factory = session_factory
        self._box = box

    async def get(self, platform: str) -> StoredSession | None:
        async with self._session_factory() as db:
            row = await db.get(PlatformSession, platform)
        if row is None:
            return None
        cookie = self._box.decrypt(row.secret)
        if cookie is None:
            return None
        return StoredSession(platform, cookie, row.account_name, row.verified, row.updated_at)

    async def updated_at(self, platform: str) -> datetime | None:
        async with self._session_factory() as db:
            value: datetime | None = await db.scalar(
                select(PlatformSession.updated_at).where(PlatformSession.platform == platform)
            )
        return value

    async def save(self, platform: str, cookie: str, account_name: str, verified: bool) -> None:
        values = {
            "platform": platform,
            "secret": self._box.encrypt(cookie),
            "account_name": account_name,
            "verified": verified,
            "updated_at": datetime.now(UTC),
        }
        statement = insert(PlatformSession).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[PlatformSession.platform],
            set_={name: statement.excluded[name] for name in values if name != "platform"},
        )
        async with self._session_factory() as db:
            await db.execute(statement)
            await db.commit()

    async def delete(self, platform: str) -> None:
        async with self._session_factory() as db:
            await db.execute(delete(PlatformSession).where(PlatformSession.platform == platform))
            await db.commit()


class SessionSync:
    """Keeps one source on the latest stored session. apply(None) goes back to the .env one."""

    def __init__(
        self,
        store: SessionStore,
        platform: str,
        apply: Callable[[StoredSession | None], None],
    ) -> None:
        self._store = store
        self._platform = platform
        self._apply = apply
        self._applied: datetime | None = None
        self._checked_at = 0.0

    async def ensure_current(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._checked_at < SYNC_INTERVAL_SECONDS:
            return
        self._checked_at = now
        try:
            latest = await self._store.updated_at(self._platform)
            if latest == self._applied:
                return
            stored = await self._store.get(self._platform) if latest else None
        except SQLAlchemyError as exc:
            # Keep using whatever session is loaded, scraping should not stop over this.
            logger.warning("could not check %s session: %s", self._platform, exc)
            return
        self._apply(stored)
        self._applied = latest
        logger.info(
            "%s session switched to %s",
            self._platform,
            f"web session for {stored.account_name}" if stored else ".env session",
        )
