"""Database operations for folders, media items, blobs and download jobs."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Blob, DownloadJob, Folder, MediaItem
from app.library.storage import DownloadedFile, LibraryStorage

logger = logging.getLogger(__name__)


SOURCES = ("post", "story", "upload")


def source_expression() -> Any:
    """Where an item came from, derived from existing columns: no extra column to keep in sync."""
    return case(
        (MediaItem.platform == "upload", "upload"),
        (MediaItem.is_story.is_(True), "story"),
        else_="post",
    )


@dataclass(frozen=True)
class FolderSummary:
    folder: Folder
    photo_count: int
    video_count: int
    size_bytes: int
    # counts[source][media_type], e.g. counts["story"]["video"]
    counts: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class ItemSource:
    """Where a file in a folder came from: a platform post, or a manual upload."""

    platform: str
    author_username: str
    source_post_id: str
    item_index: int
    is_story: bool = False
    caption: str = ""
    permalink: str | None = None
    posted_at: datetime | None = None


async def add_file_to_folder(
    db: AsyncSession,
    storage: LibraryStorage,
    folder_id: int,
    downloaded: DownloadedFile,
    source: ItemSource,
) -> MediaItem | None:
    """Store the file once per sha256 and link it to the folder. None when it was already there."""
    blob = await db.get(Blob, downloaded.sha256)
    if blob is None:
        stored = await storage.store(downloaded)
        blob = Blob(
            sha256=downloaded.sha256,
            media_type=stored.media_type,
            content_type=stored.content_type,
            extension=stored.extension,
            size_bytes=stored.size_bytes,
            original_size_bytes=downloaded.size_bytes,
            has_thumbnail=stored.has_thumbnail,
        )
        db.add(blob)
    else:
        # Same bytes already stored for another folder or post: reuse them.
        storage.discard(downloaded)

    item = MediaItem(
        folder_id=folder_id,
        blob_sha256=blob.sha256,
        media_type=blob.media_type,
        platform=source.platform,
        author_username=source.author_username,
        source_post_id=source.source_post_id,
        item_index=source.item_index,
        is_story=source.is_story,
        caption=source.caption,
        permalink=source.permalink,
        posted_at=source.posted_at,
    )
    db.add(item)
    try:
        await db.commit()
    except IntegrityError:
        # Already in this folder (same source), or the folder was deleted meanwhile.
        await db.rollback()
        logger.info(
            "%s#%d not saved: duplicate or folder gone", source.source_post_id, source.item_index
        )
        return None
    await db.refresh(item, ["blob"])
    return item


def normalize_folder_name(name: str) -> str:
    return " ".join(name.split())


async def get_or_create_folder(db: AsyncSession, name: str) -> Folder:
    clean = normalize_folder_name(name)
    folder = await db.scalar(select(Folder).where(func.lower(Folder.name) == clean.lower()))
    if folder is None:
        folder = Folder(name=clean)
        db.add(folder)
        await db.flush()
    return folder


async def create_job(
    db: AsyncSession, folder: Folder, platform: str, username: str, post_ids: list[str]
) -> DownloadJob:
    job = DownloadJob(
        id=str(uuid.uuid4()),
        folder_id=folder.id,
        platform=platform,
        username=username,
        post_ids=post_ids,
        status="queued",
        total_posts=len(post_ids),
        errors=[],
    )
    db.add(job)
    await db.flush()
    return job


async def list_folders(db: AsyncSession) -> list[FolderSummary]:
    photo = func.count(case((MediaItem.media_type == "photo", 1)))
    video = func.count(case((MediaItem.media_type == "video", 1)))
    size = func.coalesce(func.sum(Blob.size_bytes), 0)
    rows = await db.execute(
        select(Folder, photo, video, size)
        .outerjoin(MediaItem, MediaItem.folder_id == Folder.id)
        .outerjoin(Blob, Blob.sha256 == MediaItem.blob_sha256)
        .group_by(Folder.id)
        .order_by(Folder.created_at.desc())
    )
    folders = rows.all()

    source = source_expression()
    count_rows = await db.execute(
        select(MediaItem.folder_id, source, MediaItem.media_type, func.count()).group_by(
            MediaItem.folder_id, source, MediaItem.media_type
        )
    )
    counts: dict[int, dict[str, dict[str, int]]] = {}
    for folder_id, item_source, media_type, count in count_rows.all():
        per_folder = counts.setdefault(
            folder_id, {name: {"photo": 0, "video": 0} for name in SOURCES}
        )
        per_folder[item_source][media_type] = int(count)

    empty = {name: {"photo": 0, "video": 0} for name in SOURCES}
    return [
        FolderSummary(f, int(p), int(v), int(s), counts.get(f.id, empty)) for f, p, v, s in folders
    ]


async def get_folder_summary(db: AsyncSession, folder_id: int) -> FolderSummary | None:
    summaries = [s for s in await list_folders(db) if s.folder.id == folder_id]
    return summaries[0] if summaries else None


async def list_media(
    db: AsyncSession, folder_id: int, media_type: str, source: str | None = None
) -> list[MediaItem]:
    conditions = [MediaItem.folder_id == folder_id, MediaItem.media_type == media_type]
    if source is not None:
        conditions.append(source_expression() == source)
    result = await db.scalars(
        select(MediaItem)
        .where(*conditions)
        .order_by(MediaItem.posted_at.desc().nulls_last(), MediaItem.item_index)
    )
    return list(result.all())


async def delete_orphan_blobs(db: AsyncSession, storage: LibraryStorage, shas: set[str]) -> int:
    """Remove blobs no folder points at anymore. Call after the referencing rows are gone."""
    if not shas:
        return 0
    still_used = set(
        (
            await db.scalars(
                select(MediaItem.blob_sha256).where(MediaItem.blob_sha256.in_(shas)).distinct()
            )
        ).all()
    )
    orphans = list((await db.scalars(select(Blob).where(Blob.sha256.in_(shas - still_used)))).all())
    for blob in orphans:
        await db.delete(blob)
    await db.commit()
    # Files go after the commit, so a failed transaction never leaves rows without files.
    for blob in orphans:
        storage.delete_blob_files(blob.sha256, blob.extension)
    return len(orphans)


async def delete_folder(db: AsyncSession, storage: LibraryStorage, folder: Folder) -> None:
    shas = set(
        (
            await db.scalars(select(MediaItem.blob_sha256).where(MediaItem.folder_id == folder.id))
        ).all()
    )
    await db.execute(delete(MediaItem).where(MediaItem.folder_id == folder.id))
    await db.delete(folder)
    await db.commit()
    removed = await delete_orphan_blobs(db, storage, shas)
    logger.info("deleted folder %s, %d files removed from disk", folder.id, removed)


async def delete_item(db: AsyncSession, storage: LibraryStorage, item: MediaItem) -> None:
    sha = item.blob_sha256
    await db.delete(item)
    await db.commit()
    await delete_orphan_blobs(db, storage, {sha})
