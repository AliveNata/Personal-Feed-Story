"""TikTok feed via the DouK-Downloader Web API (JoeanAmier/TikTokDownloader)."""

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.errors import PrivateAccountError, UpstreamError, UserNotFoundError
from app.library.types import ResolvedPost
from app.models import FeedPage, MediaSlide, Post, proxied_media_url
from app.ratelimit import RateBudget
from app.sources import ProfileProbe

logger = logging.getLogger(__name__)

PLATFORM = "tiktok"
# Load more walks back in windows of lookback_days, up to a year.
MAX_HISTORY_DAYS = 365
VIDEO_URL_HINT = re.compile(r"video|\.mp4|mime_type=video", re.IGNORECASE)

# Every TikTok secUid starts with this base64 prefix, so we can skip the lookup.
SEC_UID_PREFIX = "MS4wLjABAAAA"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _video_count(user_info: dict[str, Any]) -> int | None:
    # Newer responses carry statsV2 with string counts next to (or instead of) stats.
    for key in ("stats", "statsV2"):
        stats = user_info.get(key)
        if isinstance(stats, dict) and str(stats.get("videoCount", "")).isdigit():
            return int(stats["videoCount"])
    return None


def douk_slides(item: dict[str, Any]) -> list[MediaSlide]:
    cover = str(item.get("static_cover") or "")
    downloads = item.get("downloads")
    if isinstance(downloads, str) and downloads:
        return [
            MediaSlide(
                type="video",
                url=proxied_media_url(downloads),
                poster_url=proxied_media_url(cover) or None,
            )
        ]
    if isinstance(downloads, list):
        # Slideshows are photos, "live" slides are short clips: tell them apart by URL.
        return [
            MediaSlide(
                type="video" if VIDEO_URL_HINT.search(str(url)) else "photo",
                url=proxied_media_url(str(url)),
            )
            for url in downloads
            if url
        ]
    return []


def map_douk_item(item: dict[str, Any]) -> Post:
    """Map one extracted DouK item (source=false) to our Post shape."""
    cover = str(item.get("static_cover") or "")
    downloads = item.get("downloads")
    if not cover and isinstance(downloads, list) and downloads:
        # Photo slideshows have no video cover, so use the first slide.
        cover = str(downloads[0])

    timestamp = int(item.get("create_timestamp") or 0)
    return Post(
        id=str(item["id"]),
        image_url=proxied_media_url(cover),
        caption=str(item.get("desc") or ""),
        likes=_count(item.get("digg_count")),
        comments=_count(item.get("comment_count")),
        shares=_count(item.get("share_count")),
        created_at=datetime.fromtimestamp(timestamp, tz=UTC),
        is_story=False,
        permalink=item.get("share_url") or None,
        media=douk_slides(item),
    )


def douk_media_urls(item: dict[str, Any]) -> list[str]:
    """Original media of a DouK item: one video URL, or a list of slides (photos or live clips)."""
    downloads = item.get("downloads")
    if isinstance(downloads, list):
        return [str(url) for url in downloads if url]
    if isinstance(downloads, str) and downloads:
        return [downloads]
    return []


def _count(value: Any) -> int:
    # DouK returns -1 when a stat is missing.
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


class TikTokSource:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        douk_base_url: str,
        cookie: str,
        lookback_days: int,
        budget: RateBudget | None = None,
    ) -> None:
        self._http = http_client
        self._budget = budget
        self._douk_base_url = douk_base_url.rstrip("/")
        self._cookie = cookie
        self._lookback_days = lookback_days
        # secUid never changes for an account, so one lookup per username is enough.
        self._sec_uid_cache: dict[str, str] = {}

    async def _post_douk(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._budget is not None:
            await asyncio.to_thread(self._budget.ensure_available, PLATFORM)
            self._budget.record(PLATFORM)
        try:
            response = await self._http.post(f"{self._douk_base_url}{path}", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("douk request %s failed: %s", path, exc)
            raise UpstreamError("DouK API tidak bisa dihubungi") from exc
        body: dict[str, Any] = response.json()
        return body

    async def _user_info(self, username: str) -> dict[str, Any]:
        """TikTok user detail through DouK's signed call (see douk/user_route.py).

        Scraping the profile HTML directly gets a WAF challenge page, even with a browser
        TLS fingerprint.
        """
        body = await self._post_douk(
            "/tiktok/user", {"unique_id": username, "cookie": self._cookie}
        )
        data = body.get("data")
        user = data.get("user") if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(user, dict) or not user.get("secUid"):
            logger.warning("douk user lookup failed for %s: %s", username, body.get("message"))
            raise UserNotFoundError(
                f"Akun TikTok @{username} tidak ditemukan, atau cookie TikTok ditolak"
            )
        self._sec_uid_cache[username.lower()] = str(user["secUid"])
        return data

    async def resolve_sec_uid(self, username: str) -> str:
        if username.startswith(SEC_UID_PREFIX):
            return username
        if cached := self._sec_uid_cache.get(username.lower()):
            return cached
        data = await self._user_info(username)
        return str(data["user"]["secUid"])

    async def probe(self, username: str) -> ProfileProbe:
        data = await self._user_info(username)
        user: dict[str, Any] = data["user"]
        avatar = user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb")
        return ProfileProbe(
            avatar_url=str(avatar or ""),
            post_count=_video_count(data),
            # DouK has no TikTok story support.
            latest_story_at=None,
        )

    async def fetch_page(self, username: str, cursor: str | None) -> FeedPage:
        """One page = posts from one lookback window. Cursor is the window index, 0 = newest."""
        window = int(cursor) if cursor and cursor.isdigit() else 0
        sec_uid = await self.resolve_sec_uid(username)
        payload: dict[str, Any] = {
            "sec_user_id": sec_uid,
            "tab": "post",
            # DouK counts "earliest" back from "latest", both in days.
            "earliest": self._lookback_days,
            "latest": window * self._lookback_days,
            "count": 35,
            "cookie": self._cookie,
            "source": False,
        }
        body = await self._post_douk("/tiktok/account", payload)
        data = body.get("data")
        older_exists = (window + 1) * self._lookback_days < MAX_HISTORY_DAYS
        next_cursor = str(window + 1) if older_exists else None

        if data is None:
            # DouK collapses "private", "no posts in range" and "blocked" into data=null.
            logger.warning("douk returned no data for %s: %s", username, body.get("message"))
            if window == 0:
                raise PrivateAccountError(
                    "Tidak ada data. Akun mungkin private, belum posting dalam "
                    f"{self._lookback_days} hari terakhir, atau cookie TikTok kedaluwarsa"
                )
            return FeedPage(posts=[], next_cursor=next_cursor, stories_status="not_supported")

        items = data if isinstance(data, list) else [data]
        posts = [map_douk_item(item) for item in items if item.get("id")]
        posts.sort(key=lambda post: post.created_at, reverse=True)
        return FeedPage(posts=posts, next_cursor=next_cursor, stories_status="not_supported")

    async def resolve_media(self, username: str, post_id: str) -> ResolvedPost:
        body = await self._post_douk(
            "/tiktok/detail", {"detail_id": post_id, "cookie": self._cookie, "source": False}
        )
        item = body.get("data")
        if not isinstance(item, dict) or not item.get("id"):
            raise UserNotFoundError(f"Post TikTok {post_id} tidak ditemukan atau sudah dihapus")

        timestamp = int(item.get("create_timestamp") or 0)
        headers = {"Referer": "https://www.tiktok.com/"}
        if self._cookie:
            # TikTok video CDN rejects requests without the session cookies.
            headers["Cookie"] = self._cookie
        return ResolvedPost(
            post_id=post_id,
            author_username=username,
            caption=str(item.get("desc") or ""),
            permalink=item.get("share_url") or None,
            posted_at=datetime.fromtimestamp(timestamp, tz=UTC) if timestamp else None,
            is_story=False,
            media_urls=douk_media_urls(item),
            request_headers=headers,
        )
