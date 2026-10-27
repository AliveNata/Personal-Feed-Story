"""TikTok feed via the DouK-Downloader Web API (JoeanAmier/TikTokDownloader)."""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.errors import PrivateAccountError, UpstreamError, UserNotFoundError
from app.models import Post, proxied_media_url

logger = logging.getLogger(__name__)

# Every TikTok secUid starts with this base64 prefix, so we can skip the lookup.
SEC_UID_PREFIX = "MS4wLjABAAAA"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


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
    )


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
    ) -> None:
        self._http = http_client
        self._douk_base_url = douk_base_url.rstrip("/")
        self._cookie = cookie
        self._lookback_days = lookback_days
        # secUid never changes for an account, so one lookup per username is enough.
        self._sec_uid_cache: dict[str, str] = {}

    async def _post_douk(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.post(f"{self._douk_base_url}{path}", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("douk request %s failed: %s", path, exc)
            raise UpstreamError("DouK API tidak bisa dihubungi") from exc
        body: dict[str, Any] = response.json()
        return body

    async def resolve_sec_uid(self, username: str) -> str:
        """Look up secUid through DouK's signed user detail call (see douk/user_route.py).

        Scraping the profile HTML directly gets a WAF challenge page, even with a browser
        TLS fingerprint.
        """
        if username.startswith(SEC_UID_PREFIX):
            return username
        cache_key = username.lower()
        if cached := self._sec_uid_cache.get(cache_key):
            return cached

        body = await self._post_douk(
            "/tiktok/user", {"unique_id": username, "cookie": self._cookie}
        )
        data = body.get("data") or {}
        sec_uid = (data.get("user") or {}).get("secUid") if isinstance(data, dict) else None
        if not sec_uid:
            logger.warning("douk user lookup failed for %s: %s", username, body.get("message"))
            raise UserNotFoundError(
                f"Akun TikTok @{username} tidak ditemukan, atau cookie TikTok ditolak"
            )

        self._sec_uid_cache[cache_key] = str(sec_uid)
        return str(sec_uid)

    async def fetch(self, username: str, limit: int) -> list[Post]:
        sec_uid = await self.resolve_sec_uid(username)
        payload = {
            "sec_user_id": sec_uid,
            "tab": "post",
            "earliest": self._lookback_days,
            "count": min(limit, 35),
            "cookie": self._cookie,
            "source": False,
        }
        body = await self._post_douk("/tiktok/account", payload)
        data = body.get("data")
        if data is None:
            # DouK collapses "private", "no posts in range" and "blocked" into data=null.
            logger.warning("douk returned no data for %s: %s", username, body.get("message"))
            raise PrivateAccountError(
                "Tidak ada data. Akun mungkin private, belum posting dalam "
                f"{self._lookback_days} hari terakhir, atau cookie TikTok kedaluwarsa"
            )

        items = data if isinstance(data, list) else [data]
        posts = [map_douk_item(item) for item in items if item.get("id")]
        posts.sort(key=lambda post: post.created_at, reverse=True)
        return posts[:limit]
