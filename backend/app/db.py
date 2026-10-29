from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    items: Mapped[list["MediaItem"]] = relationship(
        back_populates="folder", cascade="all, delete-orphan", passive_deletes=True
    )


# Folder names are unique ignoring case, so "Trip" and "trip" land in the same folder.
Index("uq_folders_name_lower", func.lower(Folder.name), unique=True)


class Blob(Base):
    """One stored file, keyed by the sha256 of the original download (dedup across folders)."""

    __tablename__ = "blobs"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(10))
    content_type: Mapped[str] = mapped_column(String(100))
    extension: Mapped[str] = mapped_column(String(10))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    original_size_bytes: Mapped[int] = mapped_column(BigInteger)
    has_thumbnail: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MediaItem(Base):
    __tablename__ = "media_items"
    __table_args__ = (
        UniqueConstraint(
            "folder_id", "platform", "source_post_id", "item_index", name="uq_media_items_source"
        ),
        Index("ix_media_items_folder_type", "folder_id", "media_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("blobs.sha256"), index=True)
    media_type: Mapped[str] = mapped_column(String(10))
    platform: Mapped[str] = mapped_column(String(20))
    author_username: Mapped[str] = mapped_column(String(100))
    source_post_id: Mapped[str] = mapped_column(String(100))
    item_index: Mapped[int] = mapped_column(Integer)
    is_story: Mapped[bool] = mapped_column(Boolean, default=False)
    caption: Mapped[str] = mapped_column(Text, default="")
    permalink: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    folder: Mapped[Folder] = relationship(back_populates="items")
    blob: Mapped[Blob] = relationship(lazy="joined")


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(20))
    username: Mapped[str] = mapped_column(String(100))
    post_ids: Mapped[list[str]] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    total_posts: Mapped[int] = mapped_column(Integer)
    done_posts: Mapped[int] = mapped_column(Integer, default=0)
    failed_posts: Mapped[int] = mapped_column(Integer, default=0)
    saved_items: Mapped[int] = mapped_column(Integer, default=0)
    skipped_items: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserSession(Base):
    """Server side session. Only the sha256 of the cookie token is stored."""

    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(lazy="joined")


class Favorite(Base):
    """An account pinned to the feed page, with what was last seen there to spot updates."""

    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    username: Mapped[str] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    post_count: Mapped[int | None] = mapped_column(Integer)
    seen_post_count: Mapped[int | None] = mapped_column(Integer)
    latest_story_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_story_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CachedPage(Base):
    """Scraped data kept for a month, so viewing an account again costs no platform requests."""

    __tablename__ = "cached_pages"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    # Lowercased, to drop every page of an account at once when it changed.
    username: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (Index("ix_cached_pages_account", "platform", "username"),)


class AccountSnapshot(Base):
    """What an account looked like at the last cheap check, to tell whether to scrape again."""

    __tablename__ = "account_snapshots"

    platform: Mapped[str] = mapped_column(String(20), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), primary_key=True)
    post_count: Mapped[int | None] = mapped_column(Integer)
    latest_story_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# Usernames are case insensitive on both platforms.
Index(
    "uq_favorites_platform_username",
    Favorite.platform,
    func.lower(Favorite.username),
    unique=True,
)


def build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
