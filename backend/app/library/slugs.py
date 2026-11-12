"""Readable folder URLs: "Ayu Sastrani" becomes /library/ayu-sastrani.

Slugs are worked out from the names on every request instead of being stored, so there is
nothing to keep in sync. Folder names are unique, but two names can still give one slug
("Ayu Sastrani" and "ayu-sastrani"): the oldest folder keeps the plain slug, the others get
their id appended.
"""

import re
import unicodedata

NON_WORD = re.compile(r"[^a-z0-9]+")


def base_slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = NON_WORD.sub("-", ascii_name.lower()).strip("-")
    if not slug:
        return "folder"
    # An all digit slug would read as a folder id in the URL.
    return f"folder-{slug}" if slug.isdigit() else slug


def assign_slugs(folders: list[tuple[int, str]]) -> dict[int, str]:
    """Folder id to slug, for (id, name) pairs of every folder."""
    taken: set[str] = set()
    slugs: dict[int, str] = {}
    for folder_id, name in sorted(folders):
        slug = base_slug(name)
        if slug in taken:
            slug = f"{slug}-{folder_id}"
        taken.add(slug)
        slugs[folder_id] = slug
    return slugs
