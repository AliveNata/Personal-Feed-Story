import io
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import Folder, MediaItem
from app.library import folder_import
from app.library.storage import LibraryStorage
from tests.conftest import requires_db


def jpeg_bytes(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buffer, "JPEG")
    return buffer.getvalue()


@requires_db
async def test_each_subfolder_becomes_a_library_folder_and_files_move_out(
    tmp_path: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    root = tmp_path / "import"
    (root / "Kendra Lust" / "set 1").mkdir(parents=True)
    (root / "Unknown").mkdir()
    (root / "Kendra Lust" / "a.jpg").write_bytes(jpeg_bytes("red"))
    (root / "Kendra Lust" / "set 1" / "b.jpg").write_bytes(jpeg_bytes("blue"))
    (root / "Kendra Lust" / "notes.txt").write_text("not media")
    (root / "Unknown" / "c.jpg").write_bytes(jpeg_bytes("green"))
    (root / "Unknown" / "c copy.jpg").write_bytes(jpeg_bytes("green"))
    (root / "loose.jpg").write_bytes(jpeg_bytes("white"))
    storage = LibraryStorage(tmp_path / "library")

    # Act
    report = await folder_import.import_folders(root, "internasional", session_factory, storage)

    # Assert
    by_name = {folder.name: folder for folder in report.folders}
    assert (by_name["Kendra Lust"].saved, by_name["Kendra Lust"].ignored) == (2, 1)
    assert (by_name["Unknown"].saved, by_name["Unknown"].skipped) == (1, 1)
    assert report.loose_files == 1
    assert not (root / "Kendra Lust" / "a.jpg").exists()
    assert (root / "Kendra Lust" / "notes.txt").exists()
    async with session_factory() as db:
        folders = {f.name: f.category for f in (await db.scalars(select(Folder))).all()}
        captions = sorted((await db.scalars(select(MediaItem.caption))).all())
    assert folders == {"Kendra Lust": "internasional", "Unknown": "internasional"}
    assert captions == ["a.jpg", "b.jpg", "c copy.jpg"]
