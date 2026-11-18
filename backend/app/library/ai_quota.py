"""Last known rate limits of Groq and Anthropic, read from the headers of our own calls.

Neither offers a call that only reports limits, so every transcription or translation
leaves its headers here, and the Session page shows the latest ones with their time.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

Provider = Literal["groq", "anthropic"]
Scope = Literal["transcribe", "translate"]
SCOPES: tuple[Scope, ...] = ("transcribe", "translate")
HEADER_PREFIXES: dict[Provider, str] = {
    "groq": "x-ratelimit-",
    "anthropic": "anthropic-ratelimit-",
}
# Long enough to see yesterday's numbers, short enough not to show a stale month.
TTL_SECONDS = 7 * 24 * 60 * 60


def _key(provider: Provider, scope: Scope) -> str:
    return f"aiquota:{provider}:{scope}"


def parse_limits(provider: Provider, headers: httpx.Headers) -> list[dict[str, str]]:
    """Group limit, remaining and reset per resource, e.g. requests, tokens, audio seconds.

    Groq names them x-ratelimit-<part>-<resource>, Anthropic anthropic-ratelimit-<resource>-<part>.
    """
    prefix = HEADER_PREFIXES[provider]
    resources: dict[str, dict[str, str]] = {}
    for name, value in headers.items():
        name = name.lower()
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix) :]
        for part in ("limit", "remaining", "reset"):
            if provider == "groq" and rest.startswith(f"{part}-"):
                resource = rest[len(part) + 1 :]
            elif provider == "anthropic" and rest.endswith(f"-{part}"):
                resource = rest[: -len(part) - 1]
            else:
                continue
            resources.setdefault(resource, {"resource": resource})[part] = value
    return [resources[name] for name in sorted(resources)]


class QuotaStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def record(
        self, provider: Provider, scope: Scope, model: str, response: httpx.Response
    ) -> None:
        limits = parse_limits(provider, response.headers)
        if not limits:
            return
        payload = {
            "provider": provider,
            "scope": scope,
            "model": model,
            "limits": limits,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._redis.set(_key(provider, scope), json.dumps(payload), ex=TTL_SECONDS)
        except RedisError as exc:
            logger.warning("could not store %s quota: %s", provider, exc)

    def all(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for provider in HEADER_PREFIXES:
            for scope in SCOPES:
                try:
                    raw = self._redis.get(_key(provider, scope))
                except RedisError as exc:
                    logger.warning("could not read %s quota: %s", provider, exc)
                    continue
                if raw:
                    entries.append(json.loads(raw))  # type: ignore[arg-type]
        return entries
