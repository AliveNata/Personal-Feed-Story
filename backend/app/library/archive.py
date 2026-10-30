"""ZIP and RAR uploads: unpack with bsdtar (libarchive), keep only photos and videos.

The archive is unpacked in the worker, not in the upload request: a 2 GB archive with
videos takes minutes once thumbnails are made, longer than a proxy should hold a request.
Progress lives in Redis, like download progress.
"""

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import Folder
from app.library import events, service
from app.library.storage import (
    EXTENSION_CONTENT_TYPES,
    DownloadedFile,
    LibraryStorage,
    _is_valid_image,
)

logger = logging.getLogger(__name__)

ARCHIVE_EXTENSIONS = ("zip", "rar")
MAX_MEMBERS = 5000
STATE_TTL_SECONDS = 24 * 60 * 60
UNPACK_TIMEOUT_SECONDS = 15 * 60
CHUNK_BYTES = 1024 * 1024
# Folders that zip tools add next to the real files.
IGNORED_PARTS = {"__MACOSX", ".DS_Store", "Thumbs.db"}


class ArchiveError(Exception):
    """The archive cannot be used. The message is shown to the user."""


def is_archive(filename: str) -> bool:
    return filename.rsplit(".", 1)[-1].lower() in ARCHIVE_EXTENSIONS if "." in filename else False


def _explain_bsdtar_error(stderr: str) -> ArchiveError:
    text = stderr.lower()
    if "encrypt" in text or "passphrase" in text or "password" in text:
        return ArchiveError(
            "Arsip ini dikunci password. Buka dulu di PC, lalu zip ulang tanpa password"
        )
    if "solid" in text:
        return ArchiveError(
            "RAR versi lama (RAR4 solid) belum bisa dibuka. Ekstrak di PC, lalu zip ulang"
        )
    if "multi" in text and "volume" in text:
        return ArchiveError("Arsip terpecah (part1, part2) belum didukung. Gabung dulu jadi satu")
    return ArchiveError("Arsip rusak atau formatnya tidak dikenali")


def unpacked_size(archive: Path) -> tuple[int, int]:
    """(total bytes, member count) from the archive listing, before anything is written."""
    try:
        result = subprocess.run(
            ["bsdtar", "-tvf", str(archive)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ArchiveError("Arsip terlalu lama dibaca") from exc
    if result.returncode != 0 and not result.stdout:
        raise _explain_bsdtar_error(result.stderr)
    total = members = 0
    for line in result.stdout.splitlines():
        # ls -l style: mode, links, owner, group, size, date..., name
        parts = line.split()
        if len(parts) >= 5 and parts[4].isdigit():
            total += int(parts[4])
            members += 1
    return total, members


def unpack(archive: Path, target: Path) -> None:
    """Extract into an empty directory. bsdtar refuses absolute paths and '..' by default."""
    target.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "bsdtar",
                "-x",
                "--no-same-owner",
                "--no-same-permissions",
                "-f",
                str(archive),
                "-C",
                str(target),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=UNPACK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ArchiveError("Membuka arsip terlalu lama, coba pecah jadi beberapa arsip") from exc
    if result.returncode != 0:
        logger.warning("bsdtar failed on %s: %s", archive.name, result.stderr.strip()[:500])
        raise _explain_bsdtar_error(result.stderr)


def media_files(root: Path) -> tuple[list[tuple[Path, str]], int]:
    """(path, content type) of every photo and video, any depth, and how many files were not."""
    found: list[tuple[Path, str]] = []
    ignored = 0
    for path in sorted(root.rglob("*")):
        # Symlinks could point outside the unpack folder: never follow them.
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).parts
        if any(part in IGNORED_PARTS or part.startswith("._") for part in relative):
            continue
        content_type = EXTENSION_CONTENT_TYPES.get(path.suffix.lower().lstrip("."))
        if content_type is None:
            ignored += 1
        else:
            found.append((path, content_type))
    return found, ignored


@dataclass
class ImportState:
    status: str = "queued"  # queued, unpacking, importing, done, failed
    filename: str = ""
    folder_id: int = 0
    total: int = 0
    done: int = 0
    saved: int = 0
    skipped: int = 0
    ignored: int = 0
    failed: int = 0
    error: str | None = None
    failures: list[str] = field(default_factory=list)


class ImportStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _key(self, import_id: str) -> str:
        return f"archiveimport:{import_id}"

    def save(self, import_id: str, state: ImportState, **extra: Any) -> None:
        payload = {**asdict(state), **extra}
        try:
            self._redis.set(self._key(import_id), json.dumps(payload), ex=STATE_TTL_SECONDS)
        except RedisError as exc:
            logger.warning("could not store archive import state: %s", exc)

    def load(self, import_id: str) -> dict[str, Any] | None:
        try:
            raw = self._redis.get(self._key(import_id))
        except RedisError as exc:
            logger.warning("could not read archive import state: %s", exc)
            return None
        return json.loads(raw) if raw else None  # type: ignore[arg-type]


async def save_archive_upload(
    storage: LibraryStorage, read_chunk: Callable[[int], Awaitable[bytes]], max_bytes: int
) -> Path:
    """Stream the uploaded archive to the library tmp folder, refusing it past max_bytes."""
    target = storage.root / "tmp" / f"{uuid.uuid4().hex}.archive"
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with target.open("wb") as file:
            while chunk := await read_chunk(CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise ArchiveError(
                        f"Arsip lebih besar dari {max_bytes // (1024 * 1024)} MB, pecah dulu"
                    )
                file.write(chunk)
    except ArchiveError:
        target.unlink(missing_ok=True)
        raise
    if size == 0:
        target.unlink(missing_ok=True)
        raise ArchiveError("Arsip kosong")
    return target


def _hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


async def run_import(
    import_id: str,
    archive: Path,
    params: dict[str, Any],
    storage: LibraryStorage,
    session_factory: async_sessionmaker[AsyncSession],
    states: ImportStore,
    max_unpacked_bytes: int,
) -> ImportState:
    state = ImportState(filename=params["filename"], folder_id=params["folder_id"])
    unpack_dir = storage.root / "tmp" / f"{import_id}.unpacked"
    try:
        total, members = await asyncio.to_thread(unpacked_size, archive)
        if members > MAX_MEMBERS:
            raise ArchiveError(f"Arsip berisi lebih dari {MAX_MEMBERS} file, pecah dulu")
        if total > max_unpacked_bytes:
            raise ArchiveError(
                f"Isi arsip {total // (1024 * 1024)} MB setelah dibuka, batasnya "
                f"{max_unpacked_bytes // (1024 * 1024)} MB"
            )
        state.status = "unpacking"
        states.save(import_id, state)
        await asyncio.to_thread(unpack, archive, unpack_dir)
        archive.unlink(missing_ok=True)

        files, state.ignored = await asyncio.to_thread(media_files, unpack_dir)
        state.total = len(files)
        state.status = "importing"
        states.save(import_id, state)

        async with session_factory() as db:
            folder = await db.get(Folder, state.folder_id)
            if folder is None:
                raise ArchiveError("Folder tujuan sudah dihapus")
            folder_name = folder.name

        for path, content_type in files:
            name = path.name
            try:
                sha, size = await asyncio.to_thread(_hash, path)
                downloaded = DownloadedFile(path, sha, content_type, size)
                needs_check = downloaded.media_type == "photo" and content_type != "image/heic"
                if needs_check and not await asyncio.to_thread(_is_valid_image, path):
                    raise ArchiveError("bukan gambar yang valid")
                async with session_factory() as db:
                    item = await service.add_file_to_folder(
                        db,
                        storage,
                        state.folder_id,
                        downloaded,
                        service.ItemSource(
                            platform="upload",
                            author_username=params["username"],
                            source_post_id=f"upload-{sha[:24]}",
                            item_index=0,
                            caption=name,
                        ),
                    )
                if item is None:
                    state.skipped += 1
                else:
                    state.saved += 1
            except (ArchiveError, OSError) as exc:
                logger.warning("archive %s: %s skipped: %s", import_id, name, exc)
                state.failed += 1
                if len(state.failures) < 20:
                    state.failures.append(f"{name}: {exc}")
            finally:
                path.unlink(missing_ok=True)
            state.done += 1
            states.save(import_id, state)

        if state.saved:
            async with session_factory() as db:
                folder = await db.get(Folder, state.folder_id)
                if folder is not None:
                    events.record(
                        db, "upload", folder, state.saved, detail=f"dari arsip {state.filename}"
                    )
                    await db.commit()
        state.status = "done"
        logger.info(
            "archive %s into %s: %d saved, %d skipped, %d ignored, %d failed",
            state.filename, folder_name, state.saved, state.skipped, state.ignored, state.failed,
        )  # fmt: skip
    except ArchiveError as exc:
        state.status = "failed"
        state.error = str(exc)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(unpack_dir, ignore_errors=True)
        states.save(import_id, state)
    return state
