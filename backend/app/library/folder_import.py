"""Bring a folder of folders from disk into the Library, one Library folder per subfolder.

For collections too big for a 5 GB archive upload. Run from the backend container:

    python -m app.cli import-folders /data/import --category internasional

Each subfolder of the root becomes a Library folder with the same name (an existing folder of
that name gets the files added, keeping its own category). Files are moved into the library,
not copied: an imported file disappears from the import folder, so the disk never holds two
copies. Anything that is not a photo or video stays where it was.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import Folder
from app.library import events, service
from app.library.archive import _hash, media_files
from app.library.storage import DownloadedFile, LibraryStorage, _is_valid_image

logger = logging.getLogger(__name__)

IMPORT_AUTHOR = "import"


@dataclass
class FolderReport:
    name: str
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    ignored: int = 0


@dataclass
class ImportReport:
    folders: list[FolderReport] = field(default_factory=list)
    # Files lying in the root itself: there is no subfolder to name their Library folder.
    loose_files: int = 0


async def _import_one(
    folder_dir: Path,
    category: service.FolderCategory,
    session_factory: async_sessionmaker[AsyncSession],
    storage: LibraryStorage,
    on_file: Callable[[str], None] | None = None,
) -> FolderReport:
    report = FolderReport(name=folder_dir.name)
    files, report.ignored = await asyncio.to_thread(media_files, folder_dir)
    if not files:
        return report
    async with session_factory() as db:
        folder = await service.get_or_create_folder(db, folder_dir.name, category)
        await db.commit()
        folder_id, folder_name = folder.id, folder.name

    for path, content_type in files:
        try:
            sha, size = await asyncio.to_thread(_hash, path)
            downloaded = DownloadedFile(path, sha, content_type, size)
            needs_check = downloaded.media_type == "photo" and content_type != "image/heic"
            if needs_check and not await asyncio.to_thread(_is_valid_image, path):
                raise ValueError("bukan gambar yang valid")
            async with session_factory() as db:
                item = await service.add_file_to_folder(
                    db,
                    storage,
                    folder_id,
                    downloaded,
                    service.ItemSource(
                        platform="upload",
                        author_username=IMPORT_AUTHOR,
                        source_post_id=f"upload-{sha[:24]}",
                        item_index=0,
                        caption=path.name,
                    ),
                )
            if item is None:
                report.skipped += 1
                # Already in the folder: the duplicate copy is not needed any more.
                path.unlink(missing_ok=True)
            else:
                report.saved += 1
        except (ValueError, OSError) as exc:
            logger.warning("import %s/%s skipped: %s", folder_name, path.name, exc)
            report.failed += 1
        if on_file:
            on_file(f"{folder_name}: {report.saved + report.skipped + report.failed}/{len(files)}")

    if report.saved:
        async with session_factory() as db:
            folder_row = await db.get(Folder, folder_id)
            if folder_row is not None:
                events.record(db, "upload", folder_row, report.saved, detail="dari import folder")
                await db.commit()
    return report


async def import_folders(
    root: Path,
    category: service.FolderCategory,
    session_factory: async_sessionmaker[AsyncSession],
    storage: LibraryStorage,
    on_file: Callable[[str], None] | None = None,
) -> ImportReport:
    report = ImportReport()
    for entry in sorted(root.iterdir()):
        if entry.is_file():
            report.loose_files += 1
        elif (
            entry.is_dir() and not entry.is_symlink() and service.normalize_folder_name(entry.name)
        ):
            report.folders.append(
                await _import_one(entry, category, session_factory, storage, on_file)
            )
    return report
