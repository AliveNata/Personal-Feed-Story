"""Content addressed file store on local disk, plus optional lossy compression and thumbnails.

Layout under LIBRARY_DIR:
    blobs/<sha[:2]>/<sha>.<ext>    stored file (original, or compressed copy)
    thumbs/<sha[:2]>/<sha>.webp    480px preview for the library grid
    tmp/                           in flight downloads
"""

import asyncio
import hashlib
import logging
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from PIL import Image, ImageOps, UnidentifiedImageError

from app.library.types import MediaType

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = (480, 480)
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/avif": "avif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
}


# Upload type comes from the file extension: browsers send unreliable MIME types for
# some formats (e.g. empty for .heic on Windows).
EXTENSION_CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "heic": "image/heic",
    "avif": "image/avif",
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
}
UPLOAD_CHUNK_BYTES = 1024 * 1024


def content_type_for_upload(filename: str, declared: str | None) -> str | None:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension in EXTENSION_CONTENT_TYPES:
        return EXTENSION_CONTENT_TYPES[extension]
    declared_clean = (declared or "").split(";")[0].strip().lower()
    return declared_clean if declared_clean in CONTENT_TYPE_EXTENSIONS else None


# (bytes received so far, total bytes or None when the CDN sends no Content-Length)
ProgressCallback = Callable[[int, int | None], None]


class DownloadError(Exception):
    pass


@dataclass(frozen=True)
class DownloadedFile:
    temp_path: Path
    sha256: str
    content_type: str
    size_bytes: int

    @property
    def media_type(self) -> MediaType:
        return "video" if self.content_type.startswith("video/") else "photo"


@dataclass(frozen=True)
class StoredFile:
    media_type: MediaType
    content_type: str
    extension: str
    size_bytes: int
    has_thumbnail: bool


class LibraryStorage:
    def __init__(
        self,
        root: Path,
        *,
        compress_photos: bool = False,
        photo_webp_quality: int = 82,
        transcode_videos: bool = False,
        video_crf: int = 28,
    ) -> None:
        self.root = root
        self._compress_photos = compress_photos
        self._photo_webp_quality = photo_webp_quality
        self._transcode_videos = transcode_videos
        self._video_crf = video_crf
        for sub in ("blobs", "thumbs", "tmp"):
            (root / sub).mkdir(parents=True, exist_ok=True)

    def blob_path(self, sha256: str, extension: str) -> Path:
        return self.root / "blobs" / sha256[:2] / f"{sha256}.{extension}"

    def thumbnail_path(self, sha256: str) -> Path:
        return self.root / "thumbs" / sha256[:2] / f"{sha256}.webp"

    async def download(
        self,
        url: str,
        headers: dict[str, str],
        max_bytes: int,
        on_progress: ProgressCallback | None = None,
    ) -> DownloadedFile:
        temp_path = self.root / "tmp" / uuid.uuid4().hex
        digest = hashlib.sha256()
        size = 0
        try:
            # Browser TLS fingerprint, same as DouK: TikTok's CDN rejects plain clients.
            async with AsyncSession(impersonate="chrome") as session:
                response = await session.get(url, headers=headers, stream=True, timeout=120)
                if response.status_code != 200:
                    raise DownloadError(f"CDN membalas status {response.status_code}")
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type not in CONTENT_TYPE_EXTENSIONS:
                    raise DownloadError(f"Tipe file tidak didukung: {content_type or 'kosong'}")
                length = response.headers.get("content-length", "")
                total = int(length) if length.isdigit() else None
                with temp_path.open("wb") as file:
                    async for chunk in response.aiter_content():
                        size += len(chunk)
                        if size > max_bytes:
                            raise DownloadError("File melebihi batas ukuran download")
                        digest.update(chunk)
                        file.write(chunk)
                        if on_progress is not None:
                            on_progress(size, total)
        except RequestException as exc:
            temp_path.unlink(missing_ok=True)
            raise DownloadError(f"Gagal download: {exc}") from exc
        except DownloadError:
            temp_path.unlink(missing_ok=True)
            raise
        return DownloadedFile(temp_path, digest.hexdigest(), content_type, size)

    async def save_upload(
        self, read_chunk: Callable[[int], Awaitable[bytes]], content_type: str, max_bytes: int
    ) -> DownloadedFile:
        """Stream an uploaded file to tmp while hashing it, same result shape as a download."""
        temp_path = self.root / "tmp" / uuid.uuid4().hex
        digest = hashlib.sha256()
        size = 0
        try:
            with temp_path.open("wb") as file:
                while chunk := await read_chunk(UPLOAD_CHUNK_BYTES):
                    size += len(chunk)
                    if size > max_bytes:
                        raise DownloadError("File melebihi batas ukuran upload")
                    digest.update(chunk)
                    file.write(chunk)
        except DownloadError:
            temp_path.unlink(missing_ok=True)
            raise
        if size == 0:
            temp_path.unlink(missing_ok=True)
            raise DownloadError("File kosong")
        downloaded = DownloadedFile(temp_path, digest.hexdigest(), content_type, size)
        if downloaded.media_type == "photo" and content_type != "image/heic":
            ok = await asyncio.to_thread(_is_valid_image, temp_path)
            if not ok:
                temp_path.unlink(missing_ok=True)
                raise DownloadError("File bukan gambar yang valid")
        return downloaded

    async def store(self, downloaded: DownloadedFile) -> StoredFile:
        """Move a download into the blob store. Caller has already checked the sha is new."""
        source = downloaded.temp_path
        content_type = downloaded.content_type
        extension = CONTENT_TYPE_EXTENSIONS[content_type]
        work_files: list[Path] = [source]
        try:
            if downloaded.media_type == "photo" and self._compress_photos:
                converted = await asyncio.to_thread(self._to_webp, source)
                if converted is not None:
                    work_files.append(converted)
                    source, content_type, extension = converted, "image/webp", "webp"
            elif downloaded.media_type == "video" and self._transcode_videos:
                transcoded = await self._transcode(source)
                if transcoded is not None:
                    work_files.append(transcoded)
                    source, content_type, extension = transcoded, "video/mp4", "mp4"

            final_path = self.blob_path(downloaded.sha256, extension)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            has_thumbnail = await self._make_thumbnail(
                source, downloaded.media_type, self.thumbnail_path(downloaded.sha256)
            )
            shutil.move(source, final_path)
            return StoredFile(
                media_type=downloaded.media_type,
                content_type=content_type,
                extension=extension,
                size_bytes=final_path.stat().st_size,
                has_thumbnail=has_thumbnail,
            )
        finally:
            for path in work_files:
                path.unlink(missing_ok=True)

    def discard(self, downloaded: DownloadedFile) -> None:
        downloaded.temp_path.unlink(missing_ok=True)

    def delete_blob_files(self, sha256: str, extension: str) -> None:
        self.blob_path(sha256, extension).unlink(missing_ok=True)
        self.thumbnail_path(sha256).unlink(missing_ok=True)

    def _to_webp(self, source: Path) -> Path | None:
        target = source.with_suffix(".webp")
        try:
            with Image.open(source) as image:
                image.save(target, "WEBP", quality=self._photo_webp_quality, method=6)
        except (UnidentifiedImageError, OSError) as exc:
            logger.warning("webp conversion failed, keeping original %s: %s", source, exc)
            target.unlink(missing_ok=True)
            return None
        return target

    async def _transcode(self, source: Path) -> Path | None:
        target = source.with_suffix(".h265.mp4")
        ok = await _run_ffmpeg(
            "-i", str(source),
            "-c:v", "libx265", "-crf", str(self._video_crf), "-preset", "medium",
            # hvc1 tag lets Safari play HEVC in mp4.
            "-tag:v", "hvc1",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(target),
        )  # fmt: skip
        if not ok:
            logger.warning("h265 transcode failed, keeping original %s", source)
            target.unlink(missing_ok=True)
            return None
        return target

    async def _make_thumbnail(self, source: Path, media_type: MediaType, target: Path) -> bool:
        target.parent.mkdir(parents=True, exist_ok=True)
        if media_type == "photo":
            return await asyncio.to_thread(_photo_thumbnail, source, target)
        # Grab a frame one second in. Very short clips have no frame there, so retry at zero.
        for offset in ("1", "0"):
            if await _run_ffmpeg(
                "-ss", offset, "-i", str(source), "-frames:v", "1",
                "-vf", f"scale={THUMBNAIL_SIZE[0]}:-2", str(target),
            ):  # fmt: skip
                return True
        logger.warning("video thumbnail failed for %s", source)
        return False


def _is_valid_image(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
    except (UnidentifiedImageError, OSError):
        return False
    return True


def _photo_thumbnail(source: Path, target: Path) -> bool:
    try:
        with Image.open(source) as image:
            # Phones store portrait shots sideways plus an EXIF turn, which browsers apply to the
            # photo: turn the thumbnail the same way or the grid shows it lying down.
            upright = ImageOps.exif_transpose(image)
            upright.thumbnail(THUMBNAIL_SIZE)
            upright.convert("RGB").save(target, "WEBP", quality=75)
    except (UnidentifiedImageError, OSError) as exc:
        logger.warning("photo thumbnail failed for %s: %s", source, exc)
        return False
    return True


async def _run_ffmpeg(*args: str) -> bool:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, stderr = await process.communicate()
    if process.returncode != 0:
        logger.warning("ffmpeg failed (%s): %s", process.returncode, stderr.decode()[-500:])
        return False
    return True
