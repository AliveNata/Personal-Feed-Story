"""Stream a ZIP of library files to the browser without building it on disk first.

Library videos run to several GB and the disk is often tight, so the archive is written
chunk by chunk into the response. Files are stored, not compressed: photos and videos are
already compressed, and deflating them only costs CPU.
"""

import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db import MediaItem

CHUNK_BYTES = 1024 * 1024
# Characters Windows and macOS refuse in file names, plus path separators.
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


@dataclass(frozen=True)
class ZipEntry:
    path: Path
    name: str


def download_name(item: MediaItem) -> str:
    """The name a file gets when saved: the original file name for uploads."""
    extension = item.blob.extension
    if item.platform == "upload" and item.caption:
        name = UNSAFE_NAME.sub("_", item.caption).strip(" .")
        if name:
            return name if name.lower().endswith(f".{extension}") else f"{name}.{extension}"
    stem = f"{item.platform}_{item.author_username}_{item.source_post_id}_{item.item_index}"
    return f"{stem}.{extension}"


def unique_names(names: list[str]) -> list[str]:
    """Two uploads called IMG_1.jpg would overwrite each other when unzipped: number them."""
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        stem, dot, extension = name.rpartition(".")
        if not dot:
            stem, extension = name, ""
        candidate, counter = name, 2
        while candidate.lower() in seen:
            candidate = f"{stem} ({counter}){dot}{extension}"
            counter += 1
        seen.add(candidate.lower())
        result.append(candidate)
    return result


class _ChunkSink:
    """A write only, non seekable file: zipfile then writes sizes after each entry."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._offset = 0

    def write(self, data: bytes) -> int:
        self._parts.append(bytes(data))
        self._offset += len(data)
        return len(data)

    def tell(self) -> int:
        return self._offset

    def flush(self) -> None:
        return None

    def drain(self) -> bytes:
        data = b"".join(self._parts)
        self._parts.clear()
        return data


def stream_zip(entries: list[ZipEntry]) -> Iterator[bytes]:
    """Yield the ZIP in pieces. Blocking file IO: run it in a worker thread."""
    sink = _ChunkSink()
    # zipfile only needs write, tell and flush from the file it is given.
    target_file: Any = sink
    with zipfile.ZipFile(
        target_file, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as archive:
        for entry in entries:
            info = zipfile.ZipInfo.from_file(entry.path, arcname=entry.name)
            info.compress_type = zipfile.ZIP_STORED
            with (
                entry.path.open("rb") as source,
                archive.open(info, "w", force_zip64=True) as target,
            ):
                while chunk := source.read(CHUNK_BYTES):
                    target.write(chunk)
                    yield sink.drain()
            yield sink.drain()
    yield sink.drain()
