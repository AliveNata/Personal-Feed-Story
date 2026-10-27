import re
from urllib.parse import urlparse

# Only these CDNs are proxied, so /api/media cannot be abused as an open proxy (SSRF).
ALLOWED_MEDIA_HOST_SUFFIXES = (
    ".cdninstagram.com",
    ".fbcdn.net",
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".tiktokcdn-eu.com",
    ".tiktokv.com",
    ".ibyteimg.com",
    ".byteimg.com",
)
ALLOWED_CONTENT_TYPE_PREFIXES = ("image/", "video/")
# TikTok serves feed videos from numbered webapp hosts on tiktok.com itself.
TIKTOK_VIDEO_HOST = re.compile(r"^v\d+-webapp(-prime)?(-[a-z]+)?\.tiktok\.com$")


def is_allowed_media_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if TIKTOK_VIDEO_HOST.match(hostname):
        return True
    return f".{hostname}".endswith(ALLOWED_MEDIA_HOST_SUFFIXES)


def referer_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith((".cdninstagram.com", ".fbcdn.net")):
        return "https://www.instagram.com/"
    return "https://www.tiktok.com/"
