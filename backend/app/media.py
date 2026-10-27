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


def is_allowed_media_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = f".{parsed.hostname.lower()}"
    return host.endswith(ALLOWED_MEDIA_HOST_SUFFIXES)


def referer_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith((".cdninstagram.com", ".fbcdn.net")):
        return "https://www.instagram.com/"
    return "https://www.tiktok.com/"
