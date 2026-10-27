"""Instagram feed via instaloader. Stories and highlights need a logged in session file."""

import asyncio
import json
import logging
import math
import secrets
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any, Protocol, TypeVar

import instaloader
from instaloader.exceptions import (
    ConnectionException,
    InvalidArgumentException,
    LoginRequiredException,
    PrivateProfileNotFollowedException,
    ProfileNotExistsException,
    QueryReturnedForbiddenException,
    QueryReturnedNotFoundException,
    TooManyRequestsException,
)
from instaloader.instaloadercontext import InstaloaderContext, RateController
from redis import Redis
from redis.exceptions import RedisError

from app.errors import PrivateAccountError, RateLimitedError, UpstreamError, UserNotFoundError
from app.library.types import ResolvedPost
from app.models import FeedPage, Highlight, MediaSlide, Post, StoriesStatus, proxied_media_url
from app.ratelimit import RateBudget, wait_message

logger = logging.getLogger(__name__)

PLATFORM = "instagram"
STORY_ID_PREFIX = "story-"
CURSOR_TTL_SECONDS = 30 * 60
# Instaloader's own estimate of how long it would sleep before the next query. Anything longer
# than this means "close to the limit": stop and tell the user instead of hanging the request.
MAX_ACCEPTABLE_WAIT_SECONDS = 10
COOLDOWN_AFTER_429_SECONDS = 15 * 60

T = TypeVar("T")


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
    is_video: bool
    video_url: str | None


class InstagramSidecarNodeLike(Protocol):
    is_video: bool
    display_url: str
    video_url: str | None


class InstagramFullPostLike(Protocol):
    typename: str
    is_video: bool
    url: str
    video_url: str | None

    def get_sidecar_nodes(self) -> Iterable[InstagramSidecarNodeLike]: ...


def instagram_media_urls(post: InstagramFullPostLike) -> list[str]:
    """Every original file of a post: all carousel slides, video file instead of its cover."""
    return [slide_url for slide_url, _, _ in _raw_slides(post)]


def _raw_slides(post: InstagramFullPostLike) -> list[tuple[str, bool, str]]:
    """(file url, is_video, cover url) per slide."""
    if post.typename == "GraphSidecar":
        return [
            (
                (node.video_url or node.display_url) if node.is_video else node.display_url,
                bool(node.is_video and node.video_url),
                node.display_url,
            )
            for node in post.get_sidecar_nodes()
        ]
    if post.is_video and post.video_url:
        return [(post.video_url, True, post.url)]
    return [(post.url, False, post.url)]


def instagram_slides(post: InstagramFullPostLike) -> list[MediaSlide]:
    return [
        MediaSlide(
            type="video" if is_video else "photo",
            url=proxied_media_url(url),
            poster_url=proxied_media_url(cover) if is_video else None,
        )
        for url, is_video, cover in _raw_slides(post)
    ]


def _as_utc(value: datetime) -> datetime:
    # instaloader returns naive datetimes that are already in UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def map_instagram_post(post: InstagramPostLike, media: list[MediaSlide] | None = None) -> Post:
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
        media=media or [],
    )


def map_instagram_story_item(item: InstagramStoryItemLike) -> Post:
    video_url = item.video_url if item.is_video else None
    return Post(
        id=f"{STORY_ID_PREFIX}{item.mediaid}",
        image_url=proxied_media_url(item.url),
        caption=item.caption or "",
        likes=0,
        comments=0,
        shares=None,
        created_at=_as_utc(item.date_utc),
        is_story=True,
        permalink=None,
        media=[
            MediaSlide(
                type="video" if video_url else "photo",
                url=proxied_media_url(video_url or item.url),
                poster_url=proxied_media_url(item.url) if video_url else None,
            )
        ],
    )


class BudgetRateController(RateController):
    """Counts every Instagram query and refuses to sleep for minutes when near the limit."""

    def __init__(self, context: InstaloaderContext, budget: RateBudget) -> None:
        super().__init__(context)
        self._budget = budget

    def wait_before_query(self, query_type: str) -> None:
        wait = self.query_waittime(query_type, time.monotonic(), False)
        if wait > MAX_ACCEPTABLE_WAIT_SECONDS:
            self._budget.cooldown(PLATFORM, math.ceil(wait))
            raise TooManyRequestsException(wait_message(math.ceil(wait)))
        self._budget.record(PLATFORM)
        super().wait_before_query(query_type)

    def handle_429(self, query_type: str) -> None:
        # Instaloader would sleep here for many minutes. Fail fast and remember the cooldown.
        self._budget.cooldown(PLATFORM, COOLDOWN_AFTER_429_SECONDS)
        raise TooManyRequestsException(wait_message(COOLDOWN_AFTER_429_SECONDS))


def build_loader(
    username: str, session_file: Path | None, budget: RateBudget | None = None
) -> instaloader.Instaloader:
    loader = instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        save_metadata=False,
        max_connection_attempts=1,
        request_timeout=30.0,
        rate_controller=(lambda ctx: BudgetRateController(ctx, budget)) if budget else None,
    )
    if username and session_file is not None:
        loader.load_session_from_file(username, str(session_file))
        logger.info("instagram session loaded for %s", username)
    else:
        logger.warning(
            "no instagram session configured, stories disabled and rate limits are tight"
        )
    return loader


def _safe_cover(highlight: Any, attribute: str) -> str:
    # Cover fields come straight from IG's JSON and are missing on some highlights.
    try:
        return str(getattr(highlight, attribute) or "")
    except (KeyError, TypeError):
        logger.info("highlight %s has no %s", getattr(highlight, "title", "?"), attribute)
        return ""


class CursorStore:
    """Keeps instaloader's frozen iterators server side; they are too big for a URL."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def put(self, state: dict[str, Any]) -> str | None:
        token = secrets.token_urlsafe(16)
        try:
            self._redis.set(f"igcursor:{token}", json.dumps(state), ex=CURSOR_TTL_SECONDS)
        except RedisError as exc:
            logger.warning("could not store instagram cursor, load more disabled: %s", exc)
            return None
        return token

    def get(self, token: str) -> dict[str, Any] | None:
        try:
            raw = self._redis.get(f"igcursor:{token}")
        except RedisError as exc:
            logger.warning("could not read instagram cursor: %s", exc)
            return None
        if raw is None:
            return None
        state: dict[str, Any] = json.loads(raw)  # type: ignore[arg-type]
        return state


class InstagramSource:
    def __init__(
        self,
        loader: instaloader.Instaloader,
        include_stories: bool,
        *,
        page_size: int = 12,
        budget: RateBudget | None = None,
        cursors: CursorStore | None = None,
    ) -> None:
        self._loader = loader
        self._include_stories = include_stories
        self._page_size = page_size
        self._budget = budget
        self._cursors = cursors
        # instaloader is sync and keeps one HTTP session, so serialize calls through it.
        self._lock = threading.Lock()

    async def _run(self, func: Callable[[], T], cost: int) -> T:
        if self._budget is not None:
            await asyncio.to_thread(self._budget.ensure_available, PLATFORM, cost)
        return await asyncio.to_thread(self._guarded, func)

    def _guarded(self, func: Callable[[], T]) -> T:
        with self._lock, self._translate_errors():
            return func()

    @contextmanager
    def _translate_errors(self) -> Iterator[None]:
        try:
            yield
        except TooManyRequestsException as exc:
            wait = self._budget.status(PLATFORM).wait_seconds if self._budget else 0
            raise RateLimitedError(wait_message(wait or 60), retry_after=wait or 60) from exc
        except (ProfileNotExistsException, QueryReturnedNotFoundException) as exc:
            raise UserNotFoundError("Akun atau post Instagram tidak ditemukan") from exc
        except (PrivateProfileNotFollowedException, LoginRequiredException) as exc:
            raise PrivateAccountError("Akun ini private atau butuh login Instagram") from exc
        except QueryReturnedForbiddenException as exc:
            raise UpstreamError("Instagram menolak request (403), cek session") from exc
        except ConnectionException as exc:
            logger.error("instagram request failed: %s", exc)
            raise UpstreamError("Gagal mengambil data Instagram") from exc

    async def fetch_page(self, username: str, cursor: str | None) -> FeedPage:
        return await self._run(lambda: self._fetch_page_sync(username, cursor), cost=3)

    def _fetch_page_sync(self, username: str, cursor: str | None) -> FeedPage:
        profile = instaloader.Profile.from_username(self._loader.context, username)
        iterator = profile.get_posts()
        if cursor:
            state = self._cursors.get(cursor) if self._cursors else None
            if state is None:
                raise UpstreamError("Halaman berikutnya kedaluwarsa, klik Scrape ulang")
            try:
                iterator.thaw(instaloader.FrozenNodeIterator(**state))
            except InvalidArgumentException as exc:
                raise UpstreamError("Halaman berikutnya kedaluwarsa, klik Scrape ulang") from exc

        posts = [
            map_instagram_post(p, instagram_slides(p)) for p in islice(iterator, self._page_size)
        ]
        next_cursor = None
        if self._cursors is not None and iterator.total_index < profile.mediacount:
            next_cursor = self._cursors.put(iterator.freeze()._asdict())

        stories: list[Post] = []
        status: StoriesStatus = "none"
        if not cursor:
            stories, status = self._fetch_stories(profile.userid)
        # Pinned posts come first from instaloader, so re-sort the page by date.
        posts.sort(key=lambda post: post.created_at, reverse=True)
        return FeedPage(posts=stories + posts, next_cursor=next_cursor, stories_status=status)

    def _fetch_stories(self, user_id: int) -> tuple[list[Post], StoriesStatus]:
        if not self._include_stories or not self._loader.context.is_logged_in:
            return [], "login_required"
        try:
            items = [
                map_instagram_story_item(item)
                for story in self._loader.get_stories(userids=[user_id])
                for item in story.get_items()
            ]
        except TooManyRequestsException:
            raise
        except ConnectionException as exc:
            # Stories are a bonus, so a failure here should not drop the posts.
            logger.warning("instagram stories fetch failed for %s: %s", user_id, exc)
            return [], "none"
        logger.info("instagram user %s has %d active story items", user_id, len(items))
        return items, "available" if items else "none"

    async def list_highlights(self, username: str) -> tuple[str, list[Highlight]]:
        if not self._loader.context.is_logged_in:
            return "login_required", []
        return await self._run(lambda: self._list_highlights_sync(username), cost=2)

    def _list_highlights_sync(self, username: str) -> tuple[str, list[Highlight]]:
        profile = instaloader.Profile.from_username(self._loader.context, username)
        highlights = [
            Highlight(
                id=str(item.unique_id),
                title=item.title,
                cover_url=proxied_media_url(_safe_cover(item, "cover_cropped_url")),
                fallback_cover_url=proxied_media_url(_safe_cover(item, "cover_url")) or None,
                item_count=None,
            )
            for item in self._loader.get_highlights(profile.userid)
        ]
        return ("available" if highlights else "none"), highlights

    async def highlight_items(self, username: str, highlight_id: str) -> list[Post]:
        if not self._loader.context.is_logged_in:
            raise PrivateAccountError("Highlight butuh session Instagram")
        return await self._run(lambda: self._highlight_items_sync(username, highlight_id), cost=3)

    def _highlight_items_sync(self, username: str, highlight_id: str) -> list[Post]:
        profile = instaloader.Profile.from_username(self._loader.context, username)
        for highlight in self._loader.get_highlights(profile.userid):
            if str(highlight.unique_id) == highlight_id:
                return [map_instagram_story_item(item) for item in highlight.get_items()]
        raise UserNotFoundError("Highlight tidak ditemukan")

    async def resolve_media(self, username: str, post_id: str) -> ResolvedPost:
        return await self._run(lambda: self._resolve_media_sync(username, post_id), cost=1)

    def _resolve_media_sync(self, username: str, post_id: str) -> ResolvedPost:
        context = self._loader.context
        if post_id.startswith(STORY_ID_PREFIX):
            if not context.is_logged_in:
                raise PrivateAccountError("Download story butuh session Instagram")
            item = instaloader.StoryItem.from_mediaid(
                context, int(post_id.removeprefix(STORY_ID_PREFIX))
            )
            url = item.video_url if item.is_video and item.video_url else item.url
            return ResolvedPost(
                post_id=post_id,
                author_username=username,
                caption=item.caption or "",
                permalink=None,
                posted_at=_as_utc(item.date_utc),
                is_story=True,
                media_urls=[url],
                request_headers={"Referer": "https://www.instagram.com/"},
            )

        post = instaloader.Post.from_shortcode(context, post_id)
        return ResolvedPost(
            post_id=post_id,
            author_username=username,
            caption=post.caption or "",
            permalink=f"https://www.instagram.com/p/{post_id}/",
            posted_at=_as_utc(post.date_utc),
            is_story=False,
            media_urls=instagram_media_urls(post),
            request_headers={"Referer": "https://www.instagram.com/"},
        )
