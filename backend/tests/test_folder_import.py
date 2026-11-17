import io
import subprocess
from pathlib import Path

import pytest
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


async def test_lost_import_folder_stops_with_import_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: what Docker Desktop answers once its mount of a Windows drive drops
    def no_device(_self: Path) -> list[Path]:
        raise OSError(19, "No such device")

    monkeypatch.setattr(Path, "iterdir", no_device)

    # Act / Assert
    with pytest.raises(folder_import.ImportInterrupted):
        await folder_import.import_folders(
            tmp_path,
            "lokal",
            None,
            LibraryStorage(tmp_path / "library"),  # type: ignore[arg-type]
        )


@requires_db
async def test_ts_and_wmv_videos_are_converted_to_mp4_then_imported(
    tmp_path: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange: H.264 in MPEG-TS (copied as is) and WMV (needs encoding)
    folder = tmp_path / "import" / "Nikki Benz"
    folder.mkdir(parents=True)
    for name, codecs in [
        ("clip.ts", ["-c:v", "libx264", "-c:a", "aac", "-f", "mpegts"]),
        ("old.wmv", ["-c:v", "wmv2", "-c:a", "wmav2"]),
    ]:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120",
             "-f", "lavfi", "-i", "sine=duration=1", "-shortest", *codecs, str(folder / name)],
            check=True,
        )  # fmt: skip
    storage = LibraryStorage(tmp_path / "library")

    # Act
    report = await folder_import.import_folders(
        tmp_path / "import", "internasional", session_factory, storage
    )

    # Assert
    nikki = report.folders[0]
    assert (nikki.converted, nikki.saved, nikki.failed) == (2, 2, 0)
    assert list(folder.iterdir()) == []
    async with session_factory() as db:
        captions = sorted((await db.scalars(select(MediaItem.caption))).all())
    assert captions == ["clip.mp4", "old.mp4"]
