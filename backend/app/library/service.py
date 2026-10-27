"""Database operations for folders, media items, blobs and download jobs."""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Blob, DownloadJob, Folder, MediaItem
from app.library.storage import LibraryStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FolderSummary:
    folder: Folder
    photo_count: int
    video_count: int
    size_bytes: int


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
    return [FolderSummary(f, int(p), int(v), int(s)) for f, p, v, s in rows.all()]


async def get_folder_summary(db: AsyncSession, folder_id: int) -> FolderSummary | None:
    summaries = [s for s in await list_folders(db) if s.folder.id == folder_id]
    return summaries[0] if summaries else None


async def list_media(db: AsyncSession, folder_id: int, media_type: str) -> list[MediaItem]:
    result = await db.scalars(
        select(MediaItem)
        .where(MediaItem.folder_id == folder_id, MediaItem.media_type == media_type)
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
