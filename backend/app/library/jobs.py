"""Executes one download job: resolve each post, download its files, file them in the folder."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import DownloadJob, MediaItem
from app.errors import RateLimitedError, SourceError
from app.library import service
from app.library.progress import NullProgress, ProgressReporter
from app.library.storage import DownloadError, LibraryStorage
from app.library.types import MediaResolver, ResolvedPost

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
MAX_ERRORS_KEPT = 50


class JobRunner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        resolvers: Mapping[str, MediaResolver],
        storage: LibraryStorage,
        delays: Mapping[str, tuple[float, float]],
        max_download_bytes: int,
        sleep: Sleep = asyncio.sleep,
        progress: ProgressReporter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._resolvers = resolvers
        self._storage = storage
        self._delays = delays
        self._max_download_bytes = max_download_bytes
        self._sleep = sleep
        self._progress: ProgressReporter = progress or NullProgress()

    async def run(self, job_id: str) -> None:
        try:
            await self._run(job_id)
        except Exception:
            # Without this the job row would stay "running" forever after a crash.
            logger.exception("job %s crashed", job_id)
            await self._finish(job_id, "failed")
            raise
        finally:
            self._progress.clear(job_id)

    async def _run(self, job_id: str) -> None:
        async with self._session_factory() as db:
            job = await db.get(DownloadJob, job_id)
            if job is None:
                logger.warning("job %s vanished before it started (folder deleted?)", job_id)
                return
            job.status = "running"
            await db.commit()
            platform, username, folder_id = job.platform, job.username, job.folder_id
            post_ids = list(job.post_ids)

        resolver = self._resolvers[platform]
        low, high = self._delays.get(platform, (0.0, 0.0))
        for position, post_id in enumerate(post_ids):
            if position > 0:
                await self._sleep(random.uniform(low, high))
            self._progress.report(job_id, post_id, 0, force=True)
            try:
                resolved = await resolver.resolve_media(username, post_id)
                try:
                    saved, skipped = await self._save_post(job_id, folder_id, platform, resolved)
                except DownloadError:
                    if not resolved.from_cache:
                        raise
                    # Cached CDN link expired: ask the platform once for a new one.
                    logger.info(
                        "job %s post %s: cached link failed, resolving again", job_id, post_id
                    )
                    resolved = await resolver.resolve_media(username, post_id, fresh=True)
                    saved, skipped = await self._save_post(job_id, folder_id, platform, resolved)
            except RateLimitedError as exc:
                # Continuing would only dig the rate limit deeper, so fail the rest at once.
                remaining = post_ids[position:]
                await self._record(job_id, failed=len(remaining), error=(post_id, str(exc)))
                await self._finish(job_id, "failed")
                return
            except (SourceError, DownloadError) as exc:
                logger.warning("job %s post %s failed: %s", job_id, post_id, exc)
                await self._record(job_id, failed=1, error=(post_id, str(exc)))
                continue
            await self._record(job_id, done=1, saved=saved, skipped=skipped)

        await self._finish(job_id, "done")

    async def _save_post(
        self, job_id: str, folder_id: int, platform: str, post: ResolvedPost
    ) -> tuple[int, int]:
        if not post.media_urls:
            raise DownloadError("Post tidak punya media yang bisa di-download")
        saved = skipped = 0
        file_count = len(post.media_urls)

        def report(index: int, received: int, total: int | None) -> None:
            # Percent of the whole post: earlier files count as done, the current one by bytes.
            # Without a Content-Length only finished files move the number.
            fraction = min(received / total, 1.0) if total else 0.0
            percent = round((index + fraction) / file_count * 100)
            self._progress.report(job_id, post.post_id, percent)

        for index, url in enumerate(post.media_urls):
            async with self._session_factory() as db:
                exists = await db.scalar(
                    select(MediaItem.id).where(
                        MediaItem.folder_id == folder_id,
                        MediaItem.platform == platform,
                        MediaItem.source_post_id == post.post_id,
                        MediaItem.item_index == index,
                    )
                )
            if exists is not None:
                skipped += 1
                continue

            downloaded = await self._storage.download(
                url,
                post.request_headers,
                self._max_download_bytes,
                on_progress=partial(report, index),
            )
            async with self._session_factory() as db:
                item = await service.add_file_to_folder(
                    db,
                    self._storage,
                    folder_id,
                    downloaded,
                    service.ItemSource(
                        platform=platform,
                        author_username=post.author_username,
                        source_post_id=post.post_id,
                        item_index=index,
                        is_story=post.is_story,
                        caption=post.caption,
                        permalink=post.permalink,
                        posted_at=post.posted_at,
                    ),
                )
            if item is None:
                skipped += 1
                continue
            saved += 1
        return saved, skipped

    async def _record(
        self,
        job_id: str,
        *,
        done: int = 0,
        failed: int = 0,
        saved: int = 0,
        skipped: int = 0,
        error: tuple[str, str] | None = None,
    ) -> None:
        async with self._session_factory() as db:
            job = await db.get(DownloadJob, job_id)
            if job is None:
                return
            job.done_posts += done
            job.failed_posts += failed
            job.saved_items += saved
            job.skipped_items += skipped
            if error is not None and len(job.errors) < MAX_ERRORS_KEPT:
                # Reassign so SQLAlchemy sees the JSON column change.
                job.errors = [*job.errors, {"postId": error[0], "message": error[1]}]
            await db.commit()

    async def _finish(self, job_id: str, status: str) -> None:
        async with self._session_factory() as db:
            job = await db.get(DownloadJob, job_id)
            if job is None:
                return
            job.status = status
            await db.commit()
            logger.info(
                "job %s %s: %d/%d posts, %d saved, %d skipped, %d failed",
                job_id, status, job.done_posts, job.total_posts,
                job.saved_items, job.skipped_items, job.failed_posts,
            )  # fmt: skip
