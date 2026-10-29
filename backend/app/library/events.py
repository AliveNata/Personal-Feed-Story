"""History of Library changes: downloads, uploads, moves and deletes."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Folder, LibraryEvent

# Uploads arrive one request per file. Files into the same folder within this window join one
# entry, so dropping 30 files in reads as one line instead of 30.
UPLOAD_MERGE_WINDOW = timedelta(minutes=5)


def record(
    db: AsyncSession,
    action: str,
    folder: Folder,
    item_count: int,
    *,
    target: Folder | None = None,
    detail: str = "",
) -> None:
    """Add an event to the session. The caller commits it together with the change itself."""
    db.add(
        LibraryEvent(
            action=action,
            item_count=item_count,
            folder_id=folder.id,
            folder_name=folder.name,
            target_folder_id=target.id if target else None,
            target_folder_name=target.name if target else None,
            detail=detail,
        )
    )


async def record_upload(db: AsyncSession, folder: Folder, filename: str) -> None:
    latest = await db.scalar(
        select(LibraryEvent).order_by(LibraryEvent.id.desc()).limit(1).with_for_update()
    )
    now = datetime.now(UTC)
    if (
        latest is not None
        and latest.action == "upload"
        and latest.folder_id == folder.id
        and now - latest.created_at < UPLOAD_MERGE_WINDOW
    ):
        latest.item_count += 1
        latest.created_at = now
        latest.detail = f"terakhir: {filename}"
        return
    record(db, "upload", folder, 1, detail=filename)


async def list_events(
    db: AsyncSession, *, folder_id: int | None, before_id: int | None, limit: int
) -> list[LibraryEvent]:
    query = select(LibraryEvent).order_by(LibraryEvent.id.desc()).limit(limit)
    if folder_id is not None:
        query = query.where(
            or_(LibraryEvent.folder_id == folder_id, LibraryEvent.target_folder_id == folder_id)
        )
    if before_id is not None:
        query = query.where(LibraryEvent.id < before_id)
    return list((await db.scalars(query)).all())
