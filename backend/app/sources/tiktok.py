"""TikTok feed via the DouK-Downloader Web API (JoeanAmier/TikTokDownloader)."""

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from app.errors import PrivateAccountError, UpstreamError, UserNotFoundError
from app.models import Post, proxied_media_url

logger = logging.getLogger(__name__)

# Same pattern DouK uses: anchoring on "verified" picks the profile owner, not users in the page.
SEC_UID_PATTERN = re.compile(r'"verified":(?:false|true),"secUid":"([A-Za-z0-9_-]+)"')
# Every TikTok secUid starts with this base64 prefix, so we can skip the profile lookup.
SEC_UID_PREFIX = "MS4wLjABAAAA"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


# (status_code, body) for a profile URL. Injected so tests do not hit TikTok.
ProfilePageFetcher = Callable[[str, str], Awaitable[tuple[int, str]]]


async def fetch_profile_page(url: str, cookie: str) -> tuple[int, str]:
    """Fetch with a browser TLS fingerprint: TikTok's WAF answers 403 to plain httpx clients."""
    headers = {"Cookie": cookie} if cookie else {}
    try:
        # No custom User-Agent: it has to match the impersonated browser fingerprint.
        async with AsyncSession(impersonate="chrome") as session:
            response = await session.get(url, headers=headers, timeout=30, allow_redirects=True)
    except RequestException as exc:
        logger.warning("tiktok profile request failed for %s: %s", url, exc)
        raise UpstreamError("Gagal menghubungi TikTok") from exc
    return response.status_code, response.text


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
        profile_fetcher: ProfilePageFetcher = fetch_profile_page,
    ) -> None:
        self._http = http_client
        self._fetch_profile = profile_fetcher
        self._douk_base_url = douk_base_url.rstrip("/")
        self._cookie = cookie
        self._lookback_days = lookback_days

    async def resolve_sec_uid(self, username: str) -> str:
        if username.startswith(SEC_UID_PREFIX):
            return username

        status_code, body = await self._fetch_profile(
            f"https://www.tiktok.com/@{username}", self._cookie
        )

        if status_code == 404:
            raise UserNotFoundError(f"Akun TikTok @{username} tidak ditemukan")
        if status_code >= 400:
            logger.warning("tiktok profile page for %s returned %s", username, status_code)
            raise UpstreamError(
                f"TikTok membalas status {status_code}. "
                "Coba lagi, atau ganti cookie kalau terus terjadi"
            )

        match = SEC_UID_PATTERN.search(body)
        if match is None:
            # TikTok serves a 200 page even for missing users, without the user block.
            raise UserNotFoundError(f"Akun TikTok @{username} tidak ditemukan")
        return match.group(1)

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
        try:
            response = await self._http.post(f"{self._douk_base_url}/tiktok/account", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("douk request failed for %s: %s", username, exc)
            raise UpstreamError("DouK API tidak bisa dihubungi") from exc

        body: dict[str, Any] = response.json()
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
