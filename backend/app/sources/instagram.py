"""Instagram feed via instaloader. Stories need a logged in session file."""

import asyncio
import logging
import threading
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Protocol

import instaloader
from instaloader.exceptions import (
    ConnectionException,
    LoginRequiredException,
    PrivateProfileNotFollowedException,
    ProfileNotExistsException,
    QueryReturnedForbiddenException,
    TooManyRequestsException,
)

from app.errors import (
    PrivateAccountError,
    RateLimitedError,
    UpstreamError,
    UserNotFoundError,
)
from app.models import Post, proxied_media_url

logger = logging.getLogger(__name__)


class InstagramPostLike(Protocol):
    shortcode: str
    url: str
    caption: str | None
    likes: int
    comments: int
    date_utc: datetime


class InstagramStoryItemLike(Protocol):
    mediaid: int
    url: str
    caption: str | None
    date_utc: datetime


def _as_utc(value: datetime) -> datetime:
    # instaloader returns naive datetimes that are already in UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def map_instagram_post(post: InstagramPostLike) -> Post:
    return Post(
        id=post.shortcode,
        image_url=proxied_media_url(post.url),
        caption=post.caption or "",
        likes=max(post.likes, 0),
        comments=max(post.comments, 0),
        shares=None,
        created_at=_as_utc(post.date_utc),
        is_story=False,
        permalink=f"https://www.instagram.com/p/{post.shortcode}/",
    )


def map_instagram_story_item(item: InstagramStoryItemLike) -> Post:
    return Post(
        id=f"story-{item.mediaid}",
        image_url=proxied_media_url(item.url),
        caption=item.caption or "",
        likes=0,
        comments=0,
        shares=None,
        created_at=_as_utc(item.date_utc),
        is_story=True,
        permalink=None,
    )


def build_loader(username: str, session_file: Path | None) -> instaloader.Instaloader:
    loader = instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        save_metadata=False,
        max_connection_attempts=1,
        request_timeout=30.0,
    )
    if username and session_file is not None:
        loader.load_session_from_file(username, str(session_file))
        logger.info("instagram session loaded for %s", username)
    else:
        logger.warning(
            "no instagram session configured, stories disabled and rate limits are tight"
        )
    return loader


class InstagramSource:
    def __init__(self, loader: instaloader.Instaloader, include_stories: bool) -> None:
        self._loader = loader
        self._include_stories = include_stories
        # instaloader is sync and keeps one HTTP session, so serialize calls through it.
        self._lock = threading.Lock()

    async def fetch(self, username: str, limit: int) -> list[Post]:
        return await asyncio.to_thread(self._fetch_sync, username, limit)

    def _fetch_sync(self, username: str, limit: int) -> list[Post]:
        with self._lock:
            try:
                profile = instaloader.Profile.from_username(self._loader.context, username)
                posts = [map_instagram_post(p) for p in islice(profile.get_posts(), limit)]
                stories = self._fetch_stories(profile.userid)
            except ProfileNotExistsException as exc:
                raise UserNotFoundError(f"Akun Instagram @{username} tidak ditemukan") from exc
            except (PrivateProfileNotFollowedException, LoginRequiredException) as exc:
                raise PrivateAccountError(
                    f"Akun @{username} private atau butuh login Instagram"
                ) from exc
            except TooManyRequestsException as exc:
                raise RateLimitedError("Instagram rate limit, coba lagi nanti") from exc
            except QueryReturnedForbiddenException as exc:
                raise UpstreamError("Instagram menolak request (403), cek session") from exc
            except ConnectionException as exc:
                logger.error("instagram fetch failed for %s: %s", username, exc)
                raise UpstreamError("Gagal mengambil data Instagram") from exc

        # Pinned posts come first from instaloader, so re-sort by date.
        posts.sort(key=lambda post: post.created_at, reverse=True)
        return stories + posts

    def _fetch_stories(self, user_id: int) -> list[Post]:
        if not self._include_stories or not self._loader.context.is_logged_in:
            return []
        try:
            return [
                map_instagram_story_item(item)
                for story in self._loader.get_stories(userids=[user_id])
                for item in story.get_items()
            ]
        except ConnectionException as exc:
            # Stories are a bonus, so a failure here should not drop the posts.
            logger.warning("instagram stories fetch failed for %s: %s", user_id, exc)
            return []
