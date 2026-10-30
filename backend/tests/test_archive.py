import contextlib
import io
import shutil
import zipfile
from pathlib import Path

import fakeredis
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import Folder, LibraryEvent, MediaItem
from app.library import archive
from app.library.storage import LibraryStorage
from tests.conftest import requires_db

FIXTURES = Path(__file__).parent / "fixtures"
needs_bsdtar = pytest.mark.skipif(shutil.which("bsdtar") is None, reason="bsdtar not installed")


def jpeg_bytes(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buffer, "JPEG")
    return buffer.getvalue()


def make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


@needs_bsdtar
def test_media_files_keeps_photos_and_videos_at_any_depth(tmp_path: Path) -> None:
    # Arrange
    source = make_zip(
        tmp_path / "a.zip",
        {
            "Bali/hari1/a.jpg": jpeg_bytes("red"),
            "Bali/b.MP4": b"not really a video",
            "notes.txt": b"hi",
            "__MACOSX/Bali/._a.jpg": b"junk",
        },
    )

    # Act
    archive.unpack(source, tmp_path / "out")
    files, ignored = archive.media_files(tmp_path / "out")

    # Assert
    assert sorted(path.name for path, _ in files) == ["a.jpg", "b.MP4"]
    assert dict((p.name, t) for p, t in files)["b.MP4"] == "video/mp4"
    assert ignored == 1


@needs_bsdtar
def test_unpack_never_writes_outside_the_target(tmp_path: Path) -> None:
    # Arrange
    source = make_zip(tmp_path / "evil.zip", {"../../escaped.jpg": jpeg_bytes("red")})

    # Act: bsdtar either skips the entry or fails, both are fine as long as nothing escapes
    with contextlib.suppress(archive.ArchiveError):
        archive.unpack(source, tmp_path / "deep" / "out")

    # Assert
    assert not (tmp_path / "escaped.jpg").exists()
    assert not (tmp_path / "deep" / "escaped.jpg").exists()


@needs_bsdtar
def test_listing_reports_unpacked_size_and_rar_is_readable(tmp_path: Path) -> None:
    # Arrange
    source = make_zip(tmp_path / "a.zip", {"a.jpg": b"x" * 1000, "b.jpg": b"y" * 500})

    # Act
    total, members = archive.unpacked_size(source)
    rar_total, rar_members = archive.unpacked_size(FIXTURES / "rar5-subdirs.rar")
    archive.unpack(FIXTURES / "rar5-subdirs.rar", tmp_path / "rar")

    # Assert
    assert (total, members) == (1500, 2)
    assert rar_members >= 4 and rar_total > 0
    assert (tmp_path / "rar" / "sub" / "dir1" / "file1.txt").exists()


@needs_bsdtar
def test_broken_archive_gives_a_readable_error(tmp_path: Path) -> None:
    # Arrange
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04 definitely not a zip")

    # Act / Assert
    with pytest.raises(archive.ArchiveError):
        archive.unpack(broken, tmp_path / "out")


@needs_bsdtar
@requires_db
async def test_run_import_saves_media_dedups_and_records_history(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    storage = LibraryStorage(tmp_path / "library")
    async with session_factory() as db:
        folder = Folder(name="Liburan")
        db.add(folder)
        await db.commit()
        folder_id = folder.id
    red = jpeg_bytes("red")
    source = make_zip(
        tmp_path / "upload.zip",
        {"a/red.jpg": red, "b/red-copy.jpg": red, "blue.png": b"broken png", "x.txt": b"x"},
    )
    states = archive.ImportStore(fakeredis.FakeRedis())
    params = {"filename": "upload.zip", "folder_id": folder_id, "username": "aliv"}

    # Act
    state = await archive.run_import(
        "imp1", source, params, storage, session_factory, states, 10_000_000
    )

    # Assert
    assert (state.status, state.saved, state.skipped, state.failed, state.ignored) == (
        "done",
        1,
        1,
        1,
        1,
    )
    assert states.load("imp1")["status"] == "done"  # type: ignore[index]
    assert not source.exists()
    assert not list((storage.root / "tmp").glob("imp1*"))
    async with session_factory() as db:
        items = (await db.scalars(select(MediaItem))).all()
        event = await db.scalar(select(LibraryEvent))
    assert len(items) == 1
    assert event is not None and event.detail == "dari arsip upload.zip"


@needs_bsdtar
@requires_db
async def test_run_import_refuses_archives_that_unpack_too_big(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    storage = LibraryStorage(tmp_path / "library")
    source = make_zip(tmp_path / "big.zip", {"a.jpg": b"\0" * 50_000})
    states = archive.ImportStore(fakeredis.FakeRedis())

    # Act
    state = await archive.run_import(
        "imp2",
        source,
        {"filename": "big.zip", "folder_id": 1, "username": "aliv"},
        storage,
        session_factory,
        states,
        max_unpacked_bytes=10_000,
    )

    # Assert
    assert state.status == "failed"
    assert state.error is not None and "MB" in state.error
    assert not source.exists()
