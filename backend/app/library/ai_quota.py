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


class BudgetExceeded(Exception):
    pass


class ClaudeSpend:
    """Claude tokens used by this app per calendar month, priced into an estimate in USD.

    Only an estimate: the console is the truth, and another app on the same key is not counted.
    Prices are per million tokens, from the settings.
    """

    def __init__(
        self,
        redis: Redis,
        input_usd_per_mtok: float,
        output_usd_per_mtok: float,
        monthly_budget_usd: float,
    ) -> None:
        self._redis = redis
        self._input_price = input_usd_per_mtok
        self._output_price = output_usd_per_mtok
        self.budget_usd = monthly_budget_usd if monthly_budget_usd > 0 else None

    @staticmethod
    def _month() -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    def _key(self, month: str) -> str:
        return f"aispend:claude:{month}"

    def record(self, input_tokens: int, output_tokens: int) -> None:
        key = self._key(self._month())
        try:
            pipe = self._redis.pipeline()
            pipe.hincrby(key, "input", input_tokens)
            pipe.hincrby(key, "output", output_tokens)
            # Kept a little over a year, for a look back at last year's month.
            pipe.expire(key, 400 * 24 * 60 * 60)
            pipe.execute()
        except RedisError as exc:
            logger.warning("could not record claude usage: %s", exc)

    def month(self) -> dict[str, Any]:
        month = self._month()
        try:
            raw = self._redis.hgetall(self._key(month))
        except RedisError as exc:
            logger.warning("could not read claude usage: %s", exc)
            raw = {}
        values = {k.decode() if isinstance(k, bytes) else k: int(v) for k, v in raw.items()}  # type: ignore[union-attr]
        input_tokens = values.get("input", 0)
        output_tokens = values.get("output", 0)
        usd = (input_tokens * self._input_price + output_tokens * self._output_price) / 1_000_000
        return {
            "month": month,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "usd": round(usd, 4),
            "budget_usd": self.budget_usd,
        }

    def is_over_budget(self) -> bool:
        return self.budget_usd is not None and self.month()["usd"] >= self.budget_usd

    def check(self) -> None:
        if self.is_over_budget():
            raise BudgetExceeded(
                f"Budget Claude bulan ini (${self.budget_usd:g}) udah kepakai. "
                "Naikin CLAUDE_MONTHLY_BUDGET_USD di backend/.env kalau mau lanjut."
            )
