"""Photos and covers shown in the feed, kept on disk so a month old cached page still renders.

CDN links are signed and expire within hours to days. Fetching an image from the CDN does not
count against the account, but once the link is dead only a new scrape gives a fresh one, and
that does. Videos are left out: they are large and stream with Range requests.
"""

import hashlib
import logging
import os
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Images bigger than this are streamed without caching (none of the feed covers come close).
MAX_CACHED_IMAGE_BYTES = 15 * 1024 * 1024
# A little longer than the page cache, so a page never outlives its images.
MAX_AGE_SECONDS = 35 * 24 * 60 * 60


class MediaFileCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _paths(self, url: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(url.encode()).hexdigest()
        folder = self.root / digest[:2]
        return folder / digest, folder / f"{digest}.type"

    def get(self, url: str) -> tuple[Path, str] | None:
        data_path, type_path = self._paths(url)
        try:
            return data_path, type_path.read_text().strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("media cache read failed: %s", exc)
            return None

    def put(self, url: str, body: bytes, content_type: str) -> None:
        data_path, type_path = self._paths(url)
        temp_path = data_path.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            data_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_bytes(body)
            # Rename last, so a reader never sees half a file.
            os.replace(temp_path, data_path)
            type_path.write_text(content_type)
        except OSError as exc:
            # Disk full or not writable: the image was still served, just not kept.
            logger.warning("media cache write failed: %s", exc)
            temp_path.unlink(missing_ok=True)

    def purge(self, max_age_seconds: int = MAX_AGE_SECONDS) -> int:
        """Delete files not written for max_age_seconds. Returns how many were removed."""
        cutoff = time.time() - max_age_seconds
        removed = 0
        if not self.root.exists():
            return 0
        for path in self.root.glob("*/*"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                logger.warning("media cache purge skipped %s: %s", path.name, exc)
        return removed
