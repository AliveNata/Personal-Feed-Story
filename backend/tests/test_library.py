import dataclasses
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import Blob, DownloadJob, Folder, MediaItem
from app.errors import RateLimitedError, UserNotFoundError
from app.library import service
from app.library.jobs import JobRunner
from app.library.storage import (
    DownloadedFile,
    DownloadError,
    LibraryStorage,
    ProgressCallback,
    _photo_thumbnail,
)
from app.library.types import ResolvedPost
from tests.conftest import requires_db

pytestmark = requires_db

PHOTO_URL_RED = "https://cdn.test/red.jpg"
PHOTO_URL_BLUE = "https://cdn.test/blue.jpg"


def write_jpeg(path: Path, color: str) -> None:
    Image.new("RGB", (64, 48), color).save(path, "JPEG")


class FakeCdnStorage(LibraryStorage):
    """Real storage, but "downloads" are generated locally instead of fetched over HTTP."""

    def __init__(self, root: Path, **kwargs: object) -> None:
        super().__init__(root, **kwargs)  # type: ignore[arg-type]
        self.downloads: list[str] = []

    async def download(
        self,
        url: str,
        headers: dict[str, str],
        max_bytes: int,
        on_progress: ProgressCallback | None = None,
    ) -> DownloadedFile:
        import hashlib
        import uuid

        self.downloads.append(url)
        temp = self.root / "tmp" / uuid.uuid4().hex
        write_jpeg(temp, "red" if "red" in url else "blue")
        data = temp.read_bytes()
        if on_progress is not None:
            on_progress(len(data) // 2, len(data))
            on_progress(len(data), len(data))
        return DownloadedFile(temp, hashlib.sha256(data).hexdigest(), "image/jpeg", len(data))


class FakeResolver:
    def __init__(self, posts: dict[str, list[str]], fail: dict[str, Exception] | None = None):
        self.posts = posts
        self.fail = fail or {}
        self.calls: list[str] = []

    async def resolve_media(
        self, username: str, post_id: str, *, fresh: bool = False
    ) -> ResolvedPost:
        self.calls.append(post_id)
        if post_id in self.fail:
            raise self.fail[post_id]
        return ResolvedPost(
            post_id=post_id,
            author_username=username,
            caption=f"caption {post_id}",
            permalink=None,
            posted_at=datetime(2026, 10, 1, tzinfo=UTC),
            is_story=False,
            media_urls=self.posts[post_id],
        )


async def no_sleep(_seconds: float) -> None:
    return None


async def make_job(
    factory: async_sessionmaker[AsyncSession], folder_name: str, post_ids: list[str]
) -> str:
    async with factory() as db:
        folder = await service.get_or_create_folder(db, folder_name)
        job = await service.create_job(db, folder, "instagram", "someone", post_ids)
        await db.commit()
        return job.id


def make_runner(
    factory: async_sessionmaker[AsyncSession], storage: LibraryStorage, resolver: FakeResolver
) -> JobRunner:
    return JobRunner(
        factory, {"instagram": resolver}, storage, {"instagram": (0, 0)}, 10_000_000, no_sleep
    )


async def test_folder_names_are_case_insensitive(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    async with session_factory() as db:
        first = await service.get_or_create_folder(db, "Liburan  Bali")
        await db.commit()

        # Act
        second = await service.get_or_create_folder(db, "liburan bali")

    # Assert
    assert first.id == second.id
    assert second.name == "Liburan Bali"


async def test_job_saves_carousel_and_dedups_same_bytes(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange: two slides with identical bytes and one different slide
    storage = FakeCdnStorage(tmp_path)
    resolver = FakeResolver({"p1": [PHOTO_URL_RED, PHOTO_URL_BLUE], "p2": [PHOTO_URL_RED]})
    job_id = await make_job(session_factory, "Trip", ["p1", "p2"])

    # Act
    await make_runner(session_factory, storage, resolver).run(job_id)

    # Assert
    async with session_factory() as db:
        job = await db.get(DownloadJob, job_id)
        assert job is not None
        assert (job.status, job.done_posts, job.saved_items, job.failed_posts) == ("done", 2, 3, 0)
        assert await db.scalar(select(func.count()).select_from(MediaItem)) == 3
        assert await db.scalar(select(func.count()).select_from(Blob)) == 2
    blobs_on_disk = list((tmp_path / "blobs").rglob("*.jpg"))
    thumbs_on_disk = list((tmp_path / "thumbs").rglob("*.webp"))
    assert len(blobs_on_disk) == 2
    assert len(thumbs_on_disk) == 2
    assert list((tmp_path / "tmp").iterdir()) == []


async def test_rerunning_job_skips_items_already_in_folder(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    storage = FakeCdnStorage(tmp_path)
    resolver = FakeResolver({"p1": [PHOTO_URL_RED]})
    runner = make_runner(session_factory, storage, resolver)
    await runner.run(await make_job(session_factory, "Trip", ["p1"]))

    # Act
    second_job = await make_job(session_factory, "trip", ["p1"])
    await runner.run(second_job)

    # Assert
    async with session_factory() as db:
        job = await db.get(DownloadJob, second_job)
        assert job is not None
        assert (job.saved_items, job.skipped_items) == (0, 1)
    assert storage.downloads == [PHOTO_URL_RED]


async def test_rate_limit_fails_remaining_posts_without_calling_platform(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    resolver = FakeResolver(
        {"p1": [PHOTO_URL_RED], "p3": [PHOTO_URL_BLUE]},
        fail={"p2": RateLimitedError("slow down")},
    )
    job_id = await make_job(session_factory, "Trip", ["p1", "p2", "p3"])

    # Act
    await make_runner(session_factory, FakeCdnStorage(tmp_path), resolver).run(job_id)

    # Assert
    async with session_factory() as db:
        job = await db.get(DownloadJob, job_id)
        assert job is not None
        assert (job.status, job.done_posts, job.failed_posts) == ("failed", 1, 2)
        assert job.errors[0]["postId"] == "p2"
    assert resolver.calls == ["p1", "p2"]


async def test_failed_post_is_recorded_and_job_continues(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    resolver = FakeResolver({"p2": [PHOTO_URL_BLUE]}, fail={"p1": UserNotFoundError("gone")})
    job_id = await make_job(session_factory, "Trip", ["p1", "p2"])

    # Act
    await make_runner(session_factory, FakeCdnStorage(tmp_path), resolver).run(job_id)

    # Assert
    async with session_factory() as db:
        job = await db.get(DownloadJob, job_id)
        assert job is not None
        assert (job.status, job.done_posts, job.failed_posts) == ("done", 1, 1)
        assert job.errors == [{"postId": "p1", "message": "gone"}]


async def test_delete_folder_keeps_blobs_shared_with_other_folders(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange: same photo in two folders, plus one photo only in the first folder
    storage = FakeCdnStorage(tmp_path)
    resolver = FakeResolver({"p1": [PHOTO_URL_RED, PHOTO_URL_BLUE], "p2": [PHOTO_URL_RED]})
    runner = make_runner(session_factory, storage, resolver)
    await runner.run(await make_job(session_factory, "A", ["p1"]))
    await runner.run(await make_job(session_factory, "B", ["p2"]))

    # Act
    async with session_factory() as db:
        folder_a = await db.scalar(select(Folder).where(Folder.name == "A"))
        assert folder_a is not None
        await service.delete_folder(db, storage, folder_a)

    # Assert
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Blob)) == 1
        summaries = await service.list_folders(db)
    assert [(s.folder.name, s.photo_count) for s in summaries] == [("B", 1)]
    assert len(list((tmp_path / "blobs").rglob("*.jpg"))) == 1


async def test_compress_photos_stores_webp(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    storage = FakeCdnStorage(tmp_path, compress_photos=True)
    job_id = await make_job(session_factory, "Trip", ["p1"])

    # Act
    await make_runner(session_factory, storage, FakeResolver({"p1": [PHOTO_URL_RED]})).run(job_id)

    # Assert
    async with session_factory() as db:
        blob = await db.scalar(select(Blob))
        assert blob is not None
        assert (blob.extension, blob.content_type) == ("webp", "image/webp")
    assert storage.blob_path(blob.sha256, "webp").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_video_store_creates_thumbnail(tmp_path: Path) -> None:
    # Arrange
    import hashlib

    storage = LibraryStorage(tmp_path)
    clip = tmp_path / "tmp" / "clip"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
         "-t", "2", "-pix_fmt", "yuv420p", "-f", "mp4", str(clip)],
        check=True,
    )  # fmt: skip
    data = clip.read_bytes()
    downloaded = DownloadedFile(clip, hashlib.sha256(data).hexdigest(), "video/mp4", len(data))

    # Act
    stored = await storage.store(downloaded)

    # Assert
    assert (stored.media_type, stored.extension, stored.has_thumbnail) == ("video", "mp4", True)
    assert storage.thumbnail_path(downloaded.sha256).exists()
    assert not clip.exists()


class CachedThenFreshResolver(FakeResolver):
    """First answer comes "from cache" with a dead link, the fresh answer works."""

    async def resolve_media(
        self, username: str, post_id: str, *, fresh: bool = False
    ) -> ResolvedPost:
        resolved = await super().resolve_media(username, post_id)
        if fresh:
            return resolved
        return dataclasses.replace(resolved, media_urls=["https://dead"], from_cache=True)


class DeadLinkStorage(FakeCdnStorage):
    async def download(
        self,
        url: str,
        headers: dict[str, str],
        max_bytes: int,
        on_progress: ProgressCallback | None = None,
    ) -> DownloadedFile:
        if url == "https://dead":
            raise DownloadError("CDN membalas 403")
        return await super().download(url, headers, max_bytes, on_progress)


async def test_expired_cached_link_is_resolved_again(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    storage = DeadLinkStorage(tmp_path)
    resolver = CachedThenFreshResolver({"story-1": [PHOTO_URL_BLUE]})
    job_id = await make_job(session_factory, "Story", ["story-1"])

    # Act
    await make_runner(session_factory, storage, resolver).run(job_id)

    # Assert
    async with session_factory() as db:
        job = await db.get(DownloadJob, job_id)
    assert job is not None
    assert (job.status, job.done_posts, job.saved_items) == ("done", 1, 1)
    assert resolver.calls == ["story-1", "story-1"]


class RecordingProgress:
    def __init__(self) -> None:
        self.reports: list[tuple[str, int | None]] = []
        self.cleared: list[str] = []

    def report(
        self, job_id: str, post_id: str, percent: int | None, *, force: bool = False
    ) -> None:
        self.reports.append((post_id, percent))

    def clear(self, job_id: str) -> None:
        self.cleared.append(job_id)


async def test_job_reports_percent_per_post_across_its_files(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    progress = RecordingProgress()
    resolver = FakeResolver({"carousel": [PHOTO_URL_BLUE, PHOTO_URL_BLUE + "?2"]})
    job_id = await make_job(session_factory, "Progress", ["carousel"])
    runner = JobRunner(
        session_factory,
        {"instagram": resolver},
        FakeCdnStorage(tmp_path),
        {"instagram": (0, 0)},
        10_000_000,
        no_sleep,
        progress=progress,
    )

    # Act
    await runner.run(job_id)

    # Assert
    assert [percent for _, percent in progress.reports] == [0, 25, 50, 75, 100]
    assert progress.cleared == [job_id]


async def test_random_previews_skip_repeated_files_and_respect_limit(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange: same blue photo in two posts, plus a red one
    storage = FakeCdnStorage(tmp_path)
    resolver = FakeResolver({"a": [PHOTO_URL_BLUE], "b": [PHOTO_URL_BLUE], "c": [PHOTO_URL_RED]})
    job_id = await make_job(session_factory, "Preview", ["a", "b", "c"])
    await make_runner(session_factory, storage, resolver).run(job_id)

    # Act
    async with session_factory() as db:
        previews = await service.random_previews(db)
        limited = await service.random_previews(db, limit=1)
        blobs = {
            item.blob_sha256
            for item in (await db.scalars(select(MediaItem))).all()
            if item.id in next(iter(previews.values()))
        }

    # Assert
    assert len(next(iter(previews.values()))) == 2
    assert len(blobs) == 2
    assert len(next(iter(limited.values()))) == 1


def test_photo_thumbnail_follows_exif_orientation(tmp_path: Path) -> None:
    # Arrange: stored landscape, with an EXIF tag saying "turn 90 degrees" like phone portraits
    source, target = tmp_path / "phone.jpg", tmp_path / "thumb.webp"
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (600, 300), "purple").save(source, exif=exif)

    # Act
    made = _photo_thumbnail(source, target)

    # Assert
    with Image.open(target) as thumbnail:
        assert made and thumbnail.size[1] > thumbnail.size[0]
