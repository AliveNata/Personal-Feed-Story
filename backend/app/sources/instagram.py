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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any, Protocol, TypeVar

import instaloader
from instaloader.exceptions import (
    AbortDownloadException,
    ConnectionException,
    InstaloaderException,
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
from app.platform_sessions import (
    InvalidSessionError,
    SessionSync,
    StoredSession,
    parse_cookie_header,
)
from app.ratelimit import RateBudget, wait_message
from app.sources import ProfileProbe

logger = logging.getLogger(__name__)

PLATFORM = "instagram"
# Public posts are fetched logged out, so they count against the server IP, not the account.
ANON_BUDGET_KEY = "instagram_anon"
STORY_ID_PREFIX = "story-"
# Cached pages keep their cursor for load more, instaloader itself refuses one older than a day.
CURSOR_TTL_SECONDS = 24 * 60 * 60
# Instaloader's own estimate of how long it would sleep before the next query. Anything longer
# than this means "close to the limit": stop and tell the user instead of hanging the request.
MAX_ACCEPTABLE_WAIT_SECONDS = 10
COOLDOWN_AFTER_429_SECONDS = 15 * 60
# "feedback_required" is Instagram limiting actions on the account itself, so back off longer.
COOLDOWN_AFTER_FEEDBACK_SECONDS = 30 * 60
# Media URLs seen in the feed are reused for downloads, so the worker sends no extra queries.
# IG CDN links stay valid for days, half a day keeps them well inside that.
MEDIA_CACHE_TTL_SECONDS = 12 * 60 * 60
# Reels cost one query per reel (instaloader fetches each one by shortcode), so pages stay small
# and stop while most of the budget is still free for feeds and downloads.
REELS_PAGE_SIZE = 6
# Profile lookup, the reels list page, then one query per reel.
REELS_PAGE_COST = REELS_PAGE_SIZE + 2
REELS_BUDGET_SHARE = 0.6

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

    def __init__(
        self, context: InstaloaderContext, budget: RateBudget, budget_key: str = PLATFORM
    ) -> None:
        super().__init__(context)
        self._budget = budget
        self._key = budget_key

    def wait_before_query(self, query_type: str) -> None:
        wait = self.query_waittime(query_type, time.monotonic(), False)
        if wait > MAX_ACCEPTABLE_WAIT_SECONDS:
            self._budget.cooldown(self._key, math.ceil(wait))
            raise TooManyRequestsException(wait_message(math.ceil(wait)))
        self._budget.record(self._key)
        super().wait_before_query(query_type)

    def handle_429(self, query_type: str) -> None:
        # Instaloader would sleep here for many minutes. Fail fast and remember the cooldown.
        seconds = self._budget.throttled(self._key, COOLDOWN_AFTER_429_SECONDS)
        raise TooManyRequestsException(wait_message(seconds))


def build_loader(
    username: str,
    session_file: Path | None,
    budget: RateBudget | None = None,
    budget_key: str = PLATFORM,
) -> instaloader.Instaloader:
    loader = instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        save_metadata=False,
        max_connection_attempts=1,
        request_timeout=30.0,
        rate_controller=(
            (lambda ctx: BudgetRateController(ctx, budget, budget_key)) if budget else None
        ),
    )
    if username and session_file is not None:
        loader.load_session_from_file(username, str(session_file))
        logger.info("instagram session loaded for %s", username)
    elif budget_key == PLATFORM:
        logger.warning(
            "no instagram session configured, stories disabled and rate limits are tight"
        )
    return loader


def _to_highlight(item: Any) -> Highlight:
    # Full cover first: IG's cropped cover URL often carries crop coordinates outside the
    # image, and the CDN then answers 200 with a solid black 150x150 JPEG instead of an
    # error, so the UI cannot detect it. The UI crops the full cover to a circle itself.
    full = _safe_cover(item, "cover_url")
    cropped = _safe_cover(item, "cover_cropped_url")
    primary, fallback = (full, cropped) if full else (cropped, "")
    return Highlight(
        id=str(item.unique_id),
        title=item.title,
        cover_url=proxied_media_url(primary),
        fallback_cover_url=proxied_media_url(fallback) or None,
        item_count=None,
    )


def _safe_cover(highlight: Any, attribute: str) -> str:
    # Cover fields come straight from IG's JSON and are missing on some highlights.
    try:
        return str(getattr(highlight, attribute) or "")
    except (KeyError, TypeError):
        logger.info("highlight %s has no %s", getattr(highlight, "title", "?"), attribute)
        return ""


# How Instagram says "slow down" besides a plain 429: a 401 with this message, among others.
THROTTLE_HINTS = ("429", "please wait a few minutes", "too many requests", "rate limit")


def _is_throttle(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in THROTTLE_HINTS)


# The profile API fallback is limited much tighter than the rest of Instagram.
PROFILE_API_PAUSE_SECONDS = 15 * 60


def _profile_unreadable(username: str) -> UserNotFoundError:
    return UserNotFoundError(
        f"Profil @{username} tidak kebaca dari Instagram. Cek lagi ejaan username-nya. "
        "Kalau sudah benar, jalur cadangan profil Instagram lagi dibatasi, coba akun ini lagi "
        "sekitar 15 menit. Akun lain tetap bisa dibuka"
    )


class _NeedsSession(Exception):
    """A logged out request could not see the profile: retry it with the session."""


def _missing_profile() -> UserNotFoundError:
    # Instagram answers "no such user" for more than typos: age or country restricted
    # accounts, accounts that blocked the session, and sessions it is limiting.
    return UserNotFoundError(
        "Instagram tidak memberi data profil ini. Kalau username sudah benar, bisa karena "
        "akunnya dibatasi umur atau negara, memblokir akun session, atau session sedang "
        "dibatasi. Coba buka profilnya pakai akun yang sama"
    )


def _has_more(iterator: Any) -> bool:
    """Whether a NodeIterator has items left, without pulling one (that would cost a query).

    instaloader has no public API for this, so read its page state and assume more when the
    shape is unexpected.
    """
    try:
        data = iterator._data
        return bool(iterator._page_index < len(data["edges"]) or data["page_info"]["has_next_page"])
    except (AttributeError, KeyError, TypeError):
        return True


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


@dataclass(frozen=True)
class CachedMedia:
    media_urls: list[str]
    caption: str
    permalink: str | None
    posted_at: datetime
    is_story: bool


class MediaCache:
    """Original media URLs per post or story item, written when the feed shows them."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def put_many(self, entries: dict[str, CachedMedia]) -> None:
        if not entries:
            return
        try:
            pipe = self._redis.pipeline()
            for post_id, media in entries.items():
                payload = {**asdict(media), "posted_at": media.posted_at.isoformat()}
                pipe.set(f"igmedia:{post_id}", json.dumps(payload), ex=MEDIA_CACHE_TTL_SECONDS)
            pipe.execute()
        except RedisError as exc:
            # Downloads still work without it, they just cost Instagram queries.
            logger.warning("could not cache instagram media urls: %s", exc)

    def get(self, post_id: str) -> CachedMedia | None:
        try:
            raw = self._redis.get(f"igmedia:{post_id}")
        except RedisError as exc:
            logger.warning("could not read instagram media cache: %s", exc)
            return None
        if raw is None:
            return None
        data: dict[str, Any] = json.loads(raw)  # type: ignore[arg-type]
        return CachedMedia(
            media_urls=list(data["media_urls"]),
            caption=str(data["caption"]),
            permalink=data["permalink"],
            posted_at=datetime.fromisoformat(data["posted_at"]),
            is_story=bool(data["is_story"]),
        )

    def forget(self, post_id: str) -> None:
        try:
            self._redis.delete(f"igmedia:{post_id}")
        except RedisError as exc:
            logger.warning("could not drop instagram media cache entry: %s", exc)


def _story_media(item: InstagramStoryItemLike) -> CachedMedia:
    url = item.video_url if item.is_video and item.video_url else item.url
    return CachedMedia(
        media_urls=[url],
        caption=item.caption or "",
        permalink=None,
        posted_at=_as_utc(item.date_utc),
        is_story=True,
    )


def _resolved(
    post_id: str, username: str, media: CachedMedia, *, from_cache: bool = False
) -> ResolvedPost:
    return ResolvedPost(
        post_id=post_id,
        author_username=username,
        caption=media.caption,
        permalink=media.permalink,
        posted_at=media.posted_at,
        is_story=media.is_story,
        media_urls=media.media_urls,
        request_headers={"Referer": "https://www.instagram.com/"},
        from_cache=from_cache,
    )


class InstagramSource:
    def __init__(
        self,
        loader: instaloader.Instaloader,
        include_stories: bool,
        *,
        page_size: int = 12,
        budget: RateBudget | None = None,
        cursors: CursorStore | None = None,
        media_cache: MediaCache | None = None,
    ) -> None:
        self._loader = loader
        # The .env / session file loader, used again when the web session is removed.
        self._default_loader = loader
        # Logged out loader for public profiles and posts: keeps load off the account.
        self._anon_loader = build_loader("", None, budget, ANON_BUDGET_KEY) if budget else None
        # The loader of the call running under self._lock, read by the sync helpers.
        self._active_loader = loader
        self._active_key = PLATFORM
        self._session_sync: SessionSync | None = None
        self._profile_api_paused_until = 0.0
        self._include_stories = include_stories
        self._page_size = page_size
        self._budget = budget
        self._cursors = cursors
        self._media_cache = media_cache
        # instaloader is sync and keeps one HTTP session, so serialize calls through it.
        self._lock = threading.Lock()

    def set_session_sync(self, sync: SessionSync) -> None:
        self._session_sync = sync

    def use_session(self, stored: StoredSession | None) -> None:
        """Swap in a session pasted on the web, or back to the .env one with None."""
        if stored is None:
            loader = self._default_loader
        else:
            username = stored.account_name if stored.verified else ""
            loader = build_loader(username, None, self._budget)
            loader.load_session(username, parse_cookie_header(stored.cookie))
        # Wait for a running query: instaloader is not thread safe.
        with self._lock:
            self._loader = loader

    async def _sync(self) -> None:
        if self._session_sync is not None:
            await self._session_sync.ensure_current()

    async def _run(self, func: Callable[[], T], cost: int, *, anon: bool = False) -> T:
        await self._sync()
        use_anon = anon and self._anon_loader is not None
        key = ANON_BUDGET_KEY if use_anon else PLATFORM
        if self._budget is not None:
            await asyncio.to_thread(self._budget.ensure_available, key, cost)
        return await asyncio.to_thread(self._guarded, func, use_anon)

    async def _public(self, func: Callable[[], T], cost: int) -> T:
        """Logged out first. Private or hidden profiles then go through the session."""
        if self._anon_loader is None:
            return await self._run(func, cost)
        try:
            return await self._run(func, cost, anon=True)
        except _NeedsSession as exc:
            if not self._loader.context.is_logged_in:
                raise PrivateAccountError(
                    "Akun ini tidak bisa dibuka tanpa login. Pasang session Instagram dulu"
                ) from exc
            logger.info("instagram %s, retrying with the session", exc)
            return await self._run(func, cost)

    def _guarded(self, func: Callable[[], T], anon: bool = False) -> T:
        with self._lock:
            loader = self._anon_loader if anon and self._anon_loader else self._loader
            self._active_loader = loader
            self._active_key = ANON_BUDGET_KEY if anon else PLATFORM
            with self._translate_errors():
                result = func()
            if self._budget is not None and not anon and loader.context.is_logged_in:
                # A logged in request just worked, so the session is fine again.
                self._budget.clear_session_problem(PLATFORM)
            return result

    def _session_rejected(self, reason: str) -> None:
        if (
            self._budget is not None
            and self._active_key == PLATFORM
            and self._active_loader.context.is_logged_in
        ):
            logger.warning("instagram session rejected: %s", reason)
            self._budget.mark_session_problem(PLATFORM, reason)

    def _throttle_error(self, base_seconds: int) -> RateLimitedError:
        key = self._active_key
        seconds = self._budget.throttled(key, base_seconds) if self._budget else base_seconds
        if key == ANON_BUDGET_KEY:
            return RateLimitedError(
                "Instagram lagi membatasi akses tanpa login dari IP server ini. Tunggu sekitar "
                f"{math.ceil(seconds / 60)} menit. Akun Instagram lu tidak ikut kena",
                retry_after=seconds,
            )
        return RateLimitedError(wait_message(seconds), retry_after=seconds)

    @contextmanager
    def _translate_errors(self) -> Iterator[None]:
        anon = self._active_key == ANON_BUDGET_KEY
        try:
            yield
        except TooManyRequestsException as exc:
            key = self._active_key
            wait = self._budget.status(key).wait_seconds if self._budget else 0
            raise RateLimitedError(wait_message(wait or 60), retry_after=wait or 60) from exc
        except (ProfileNotExistsException, LoginRequiredException) as exc:
            if anon:
                # Private, age or region restricted profiles look empty when logged out.
                raise _NeedsSession(f"{type(exc).__name__} logged out") from exc
            if isinstance(exc, LoginRequiredException):
                # Logged in yet told to log in: Instagram dropped the session.
                self._session_rejected("Instagram minta login ulang, session sudah tidak berlaku")
                raise PrivateAccountError("Akun ini private atau butuh login Instagram") from exc
            raise _missing_profile() from exc
        except QueryReturnedNotFoundException as exc:
            logger.warning("instagram query returned 404: %s", exc)
            raise UserNotFoundError(
                "Instagram membalas 404 saat mengambil data. Cek log backend untuk query mana"
            ) from exc
        except PrivateProfileNotFollowedException as exc:
            raise PrivateAccountError("Akun ini private dan tidak di-follow akun session") from exc
        except AbortDownloadException as exc:
            # Raised for 400 "feedback_required" and similar: Instagram is limiting this account.
            logger.error("instagram aborted the request: %s", exc)
            if anon:
                raise self._throttle_error(COOLDOWN_AFTER_429_SECONDS) from exc
            seconds = (
                self._budget.throttled(PLATFORM, COOLDOWN_AFTER_FEEDBACK_SECONDS)
                if self._budget is not None
                else COOLDOWN_AFTER_FEEDBACK_SECONDS
            )
            raise RateLimitedError(
                "Instagram membatasi akun ini (feedback_required). Istirahat dulu sekitar "
                f"{math.ceil(seconds / 60)} menit, lalu coba lagi pelan pelan",
                retry_after=seconds,
            ) from exc
        except QueryReturnedForbiddenException as exc:
            self._session_rejected("Instagram menolak session (403)")
            raise UpstreamError("Instagram menolak request (403), cek session") from exc
        except ConnectionException as exc:
            logger.error("instagram request failed: %s", exc)
            if _is_throttle(exc):
                raise self._throttle_error(COOLDOWN_AFTER_429_SECONDS) from exc
            if "401" in str(exc):
                self._session_rejected(
                    "Instagram membalas 401, session sudah logout atau kedaluwarsa"
                )
            raise UpstreamError("Gagal mengambil data Instagram") from exc
        except (InstaloaderException, KeyError, TypeError, ValueError) as exc:
            # Instagram changes the shape of its pages and responses now and then. instaloader
            # then raises BadResponseException or trips over missing keys. Never let that
            # become a 500: logged out, the session may still see the data, so try it.
            logger.error("instagram response not understood (%s): %s", type(exc).__name__, exc)
            if anon:
                raise _NeedsSession(f"{type(exc).__name__} logged out") from exc
            raise UpstreamError(
                "Respons Instagram tidak bisa dibaca. Coba lagi nanti, atau Scrape ulang"
            ) from exc

    # Any, like instaloader's own untyped returns: its Post class does not match our Protocols.
    def _profile(self, username: str) -> Any:
        """Look up a profile, with a second route when the first finds nothing.

        instaloader reads the profile from the embedded data of its web page. Instagram leaves
        that data out for some accounts that do exist, so ask the web_profile_info API next.
        """
        context = self._active_loader.context
        try:
            return instaloader.Profile.from_username(context, username)
        except ProfileNotExistsException as page_error:
            if self._active_key == ANON_BUDGET_KEY:
                # The session path has the fallback below, no need to spend it twice.
                raise
            if time.monotonic() < self._profile_api_paused_until:
                raise _profile_unreadable(username) from page_error
            logger.info(
                "instagram profile page had no data for %s, trying the profile API", username
            )
            try:
                data = context.get_json(
                    "api/v1/users/web_profile_info/", params={"username": username}
                )
            except (ConnectionException, AbortDownloadException) as api_error:
                logger.warning("instagram profile API failed for %s: %s", username, api_error)
                if isinstance(api_error, TooManyRequestsException) or _is_throttle(api_error):
                    # Only this fallback endpoint is limited, the session itself still works
                    # (feeds and media load fine). A typo in the username also lands here, so
                    # pausing all of Instagram would punish the wrong thing: pause this route.
                    self._profile_api_paused_until = time.monotonic() + PROFILE_API_PAUSE_SECONDS
                    raise _profile_unreadable(username) from api_error
                raise page_error from api_error
            user = (data.get("data") or {}).get("user")
            if not isinstance(user, dict) or not user.get("id"):
                raise
            return instaloader.Profile(context, user)

    async def fetch_page(self, username: str, cursor: str | None) -> FeedPage:
        """Posts only, logged out when the profile allows it. Stories load on their own."""
        if cursor is None:
            return await self._public(lambda: self._fetch_page_sync(username, None), cost=2)
        state = await asyncio.to_thread(self._cursors.get, cursor) if self._cursors else None
        if state is None:
            raise UpstreamError("Halaman berikutnya kedaluwarsa, klik Scrape ulang")
        # Continue with the loader that made the first page: an iterator only works there.
        anon = state.pop("mode", "session") == "anon"
        return await self._run(lambda: self._fetch_page_sync(username, state), cost=2, anon=anon)

    def _fetch_page_sync(self, username: str, state: dict[str, Any] | None) -> FeedPage:
        profile = self._profile(username)
        iterator = profile.get_posts()
        if state is not None:
            try:
                iterator.thaw(instaloader.FrozenNodeIterator(**state))
            except InvalidArgumentException as exc:
                raise UpstreamError("Halaman berikutnya kedaluwarsa, klik Scrape ulang") from exc

        posts: list[Post] = []
        to_cache: dict[str, CachedMedia] = {}
        for item in islice(iterator, self._page_size):
            post = map_instagram_post(item, instagram_slides(item))
            posts.append(post)
            to_cache[post.id] = CachedMedia(
                media_urls=instagram_media_urls(item),
                caption=post.caption,
                permalink=post.permalink,
                posted_at=post.created_at,
                is_story=False,
            )
        if self._media_cache is not None:
            self._media_cache.put_many(to_cache)
        next_cursor = None
        if self._cursors is not None and iterator.total_index < profile.mediacount:
            mode = "anon" if self._active_key == ANON_BUDGET_KEY else "session"
            next_cursor = self._cursors.put({"mode": mode, **iterator.freeze()._asdict()})

        # Pinned posts come first from instaloader, so re-sort the page by date.
        posts.sort(key=lambda post: post.created_at, reverse=True)
        snapshot = None
        if state is None:
            # Same numbers a probe would fetch, already in hand: no extra query for the baseline.
            snapshot = ProfileProbe(
                avatar_url=profile.profile_pic_url_no_iphone or "",
                post_count=profile.mediacount,
                latest_story_at=None,
            )
        return FeedPage(
            posts=posts,
            next_cursor=next_cursor,
            stories_status="on_demand" if self._include_stories else "none",
            snapshot=snapshot,
        )

    async def fetch_stories(self, username: str) -> tuple[StoriesStatus, list[Post]]:
        """Active stories, only when asked: the story query is the one Instagram watches most."""
        await self._sync()
        if not self._include_stories or not self._loader.context.is_logged_in:
            return "login_required", []
        return await self._run(lambda: self._fetch_stories_sync(username), cost=2)

    def _fetch_stories_sync(self, username: str) -> tuple[StoriesStatus, list[Post]]:
        profile = self._profile(username)
        raw_items = [
            item
            for story in self._active_loader.get_stories(userids=[profile.userid])
            for item in story.get_items()
        ]
        self._cache_story_items(raw_items)
        items = [map_instagram_story_item(item) for item in raw_items]
        logger.info("instagram user %s has %d active story items", username, len(items))
        return ("available" if items else "none"), items

    async def probe(self, username: str) -> ProfileProbe:
        """Post count and avatar, logged out: one request, nothing on the account."""
        return await self._public(lambda: self._probe_sync(username), cost=1)

    def _probe_sync(self, username: str) -> ProfileProbe:
        profile = self._profile(username)
        return ProfileProbe(
            # The plain property fetches an extra iPhone endpoint when logged in.
            avatar_url=profile.profile_pic_url_no_iphone or "",
            post_count=profile.mediacount,
            # Stories are not checked anymore, they load on demand.
            latest_story_at=None,
        )

    async def fetch_reels(
        self, username: str, cursor: str | None
    ) -> tuple[str, list[Post], str | None]:
        """One page of the Reels tab: (status, reels, next cursor)."""
        await self._sync()
        if not self._loader.context.is_logged_in:
            return "login_required", [], None
        if self._budget is not None:
            wait = await asyncio.to_thread(
                self._budget.wait_for_share, PLATFORM, REELS_PAGE_COST, REELS_BUDGET_SHARE
            )
            if wait > 0:
                minutes = max(1, math.ceil(wait / 60))
                raise RateLimitedError(
                    "Reels dijeda biar akun aman, sisa jatah request Instagram disimpan buat "
                    f"feed dan download. Coba lagi sekitar {minutes} menit",
                    retry_after=wait,
                )
        posts, next_cursor = await self._run(
            lambda: self._fetch_reels_sync(username, cursor), cost=REELS_PAGE_COST
        )
        return ("available" if posts or cursor else "none"), posts, next_cursor

    def _fetch_reels_sync(self, username: str, cursor: str | None) -> tuple[list[Post], str | None]:
        profile = self._profile(username)
        iterator = profile.get_reels()
        if cursor:
            state = self._cursors.get(cursor) if self._cursors else None
            if state is None:
                raise UpstreamError("Halaman reels berikutnya kedaluwarsa, buka ulang Reels")
            try:
                iterator.thaw(instaloader.FrozenNodeIterator(**state))
            except InvalidArgumentException as exc:
                raise UpstreamError(
                    "Halaman reels berikutnya kedaluwarsa, buka ulang Reels"
                ) from exc

        posts: list[Post] = []
        for item in islice(iterator, REELS_PAGE_SIZE):
            posts.append(map_instagram_post(item, instagram_slides(item)))
            # Downloads then reuse these URLs instead of another query per reel.
            self._cache_post(item.shortcode, item)
        next_cursor = None
        if self._cursors is not None and _has_more(iterator):
            next_cursor = self._cursors.put(iterator.freeze()._asdict())
        return posts, next_cursor

    async def list_highlights(self, username: str) -> tuple[str, list[Highlight]]:
        await self._sync()
        if not self._loader.context.is_logged_in:
            return "login_required", []
        return await self._run(lambda: self._list_highlights_sync(username), cost=2)

    def _list_highlights_sync(self, username: str) -> tuple[str, list[Highlight]]:
        profile = self._profile(username)
        highlights = [
            _to_highlight(item) for item in self._active_loader.get_highlights(profile.userid)
        ]
        return ("available" if highlights else "none"), highlights

    async def highlight_items(self, username: str, highlight_id: str) -> list[Post]:
        await self._sync()
        if not self._loader.context.is_logged_in:
            raise PrivateAccountError("Highlight butuh session Instagram")
        return await self._run(lambda: self._highlight_items_sync(username, highlight_id), cost=3)

    def _highlight_items_sync(self, username: str, highlight_id: str) -> list[Post]:
        profile = self._profile(username)
        for highlight in self._active_loader.get_highlights(profile.userid):
            if str(highlight.unique_id) == highlight_id:
                raw_items = list(highlight.get_items())
                self._cache_story_items(raw_items)
                return [map_instagram_story_item(item) for item in raw_items]
        raise UserNotFoundError("Highlight tidak ditemukan")

    def _find_story_item(self, username: str, post_id: str) -> CachedMedia:
        """Find a story or highlight item through the reel lists.

        StoryItem.from_mediaid uses an old GraphQL query that Instagram answers with
        400 "feedback_required" for many accounts, so it is not used.
        """
        profile = self._profile(username)
        wanted = int(post_id.removeprefix(STORY_ID_PREFIX))
        for story in self._active_loader.get_stories(userids=[profile.userid]):
            items = list(story.get_items())
            self._cache_story_items(items)
            for item in items:
                if item.mediaid == wanted:
                    return _story_media(item)
        for highlight in self._active_loader.get_highlights(profile.userid):
            items = list(highlight.get_items())
            self._cache_story_items(items)
            for item in items:
                if item.mediaid == wanted:
                    return _story_media(item)
        raise UserNotFoundError("Story sudah kedaluwarsa atau highlight-nya sudah dihapus")

    def _cache_post(self, post_id: str, post: Any) -> None:
        if self._media_cache is not None:
            self._media_cache.put_many(
                {
                    post_id: CachedMedia(
                        media_urls=instagram_media_urls(post),
                        caption=post.caption or "",
                        permalink=f"https://www.instagram.com/p/{post_id}/",
                        posted_at=_as_utc(post.date_utc),
                        is_story=False,
                    )
                }
            )

    def _cache_story_items(self, items: Iterable[InstagramStoryItemLike]) -> None:
        if self._media_cache is not None:
            self._media_cache.put_many(
                {f"{STORY_ID_PREFIX}{item.mediaid}": _story_media(item) for item in items}
            )

    async def resolve_media(
        self, username: str, post_id: str, *, fresh: bool = False
    ) -> ResolvedPost:
        if self._media_cache is not None:
            if fresh:
                await asyncio.to_thread(self._media_cache.forget, post_id)
            elif cached := await asyncio.to_thread(self._media_cache.get, post_id):
                return _resolved(post_id, username, cached, from_cache=True)
        if post_id.startswith(STORY_ID_PREFIX):
            # A story lookup lists the profile, its stories and maybe its highlights.
            return await self._run(lambda: self._resolve_media_sync(username, post_id), cost=3)
        return await self._public(lambda: self._resolve_media_sync(username, post_id), cost=1)

    def _resolve_media_sync(self, username: str, post_id: str) -> ResolvedPost:
        context = self._active_loader.context
        if post_id.startswith(STORY_ID_PREFIX):
            if not context.is_logged_in:
                raise PrivateAccountError("Download story butuh session Instagram")
            return _resolved(post_id, username, self._find_story_item(username, post_id))

        post = instaloader.Post.from_shortcode(context, post_id)
        self._cache_post(post_id, post)
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


# instaloader's "who am I" query, the same one its test_login uses.
WHOAMI_QUERY_HASH = "d6f4427fbe92d846298cf93df0b937d3"


def validate_instagram_cookie(raw_cookie: str) -> tuple[str, bool]:
    """(account name, verified) for a pasted Cookie header. One Instagram query.

    When Instagram only says "slow down" the cookie may still be fine, so it is accepted
    unverified, named after its user id. A rejected or logged out cookie raises.
    """
    cookies = parse_cookie_header(raw_cookie)
    if not cookies.get("sessionid"):
        raise InvalidSessionError(
            "Cookie ini tidak punya sessionid. Ambil dari tab instagram.com yang sedang login"
        )
    loader = build_loader("", None)
    loader.load_session("", cookies)
    try:
        data = loader.context.graphql_query(WHOAMI_QUERY_HASH, {})
    except (ConnectionException, AbortDownloadException) as exc:
        if _is_throttle(exc):
            logger.warning("instagram throttled the session check, saving unverified")
            return f"user id {cookies.get('ds_user_id', '?')}", False
        logger.warning("instagram rejected the pasted session: %s", exc)
        raise InvalidSessionError(
            "Instagram menolak cookie ini. Login ulang lalu ambil lagi"
        ) from exc
    user = (data.get("data") or {}).get("user")
    if not user or not user.get("username"):
        raise InvalidSessionError("Cookie ini sudah logout atau kedaluwarsa. Ambil cookie baru")
    return str(user["username"]), True
