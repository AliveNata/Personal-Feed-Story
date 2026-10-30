from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import session_routes
from app.auth import hash_password
from app.db import PlatformSession, User
from app.factory import Sources
from app.main import app
from app.platform_sessions import (
    InvalidSessionError,
    SecretBox,
    SessionStore,
    SessionSync,
    StoredSession,
    parse_cookie_header,
)
from app.ratelimit import RateBudget
from tests.conftest import requires_db

PASSWORD = "correct horse battery"
IG_COOKIE = "csrftoken=abc; sessionid=SECRET123; ds_user_id=42"


def test_parse_cookie_header_tolerates_prefix_and_line_breaks() -> None:
    # Arrange
    raw = "Cookie: a=1;\n b = 2; broken; c=x=y"

    # Act
    cookies = parse_cookie_header(raw)

    # Assert
    assert cookies == {"a": "1", "b": "2", "c": "x=y"}


def test_secret_box_roundtrip_reuses_key_and_rejects_other_keys(tmp_path: Path) -> None:
    # Arrange
    token = SecretBox(tmp_path).encrypt("sessionid=SECRET")

    # Act
    same_key = SecretBox(tmp_path).decrypt(token)
    other_key = SecretBox(tmp_path / "other").decrypt(token)

    # Assert
    assert "SECRET" not in token
    assert same_key == "sessionid=SECRET"
    assert other_key is None
    assert (tmp_path / "session.key").stat().st_mode & 0o777 == 0o600


class RecordingSource:
    def __init__(self) -> None:
        self.applied: list[StoredSession | None] = []
        self.cookie = "env-cookie"

    def use_session(self, stored: StoredSession | None) -> None:
        self.applied.append(stored)
        self.cookie = stored.cookie if stored else "env-cookie"

    async def check_cookie(self, cookie: str) -> bool:
        return "sid_tt" in cookie


@requires_db
async def test_session_sync_applies_saved_cookie_and_falls_back_on_delete(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    store = SessionStore(session_factory, SecretBox(tmp_path))
    source = RecordingSource()
    sync = SessionSync(store, "instagram", source.use_session)

    # Act
    await sync.ensure_current(force=True)
    await store.save("instagram", IG_COOKIE, "erenyaan", True)
    await sync.ensure_current()  # inside the interval: no check yet
    applied_early = list(source.applied)
    await sync.ensure_current(force=True)
    await store.delete("instagram")
    await sync.ensure_current(force=True)
    async with session_factory() as db:
        assert await db.get(PlatformSession, "instagram") is None

    # Assert
    assert applied_early == []
    assert [s.account_name if s else None for s in source.applied] == ["erenyaan", None]
    assert source.cookie == "env-cookie"


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, Any]]]:
    async with session_factory() as db:
        db.add(User(username="aliv", password_hash=hash_password(PASSWORD)))
        await db.commit()
    store = SessionStore(session_factory, SecretBox(tmp_path))
    instagram, tiktok = RecordingSource(), RecordingSource()
    budget = RateBudget(fakeredis.FakeRedis(), {"instagram": (100, 900), "tiktok": (100, 900)})
    sources = Sources(
        tiktok=tiktok,  # type: ignore[arg-type]
        instagram=instagram,  # type: ignore[arg-type]
        budget=budget,
        sessions=store,
        syncs={
            "instagram": SessionSync(store, "instagram", instagram.use_session),
            "tiktok": SessionSync(store, "tiktok", tiktok.use_session),
        },
    )

    def fake_validate(cookie: str) -> tuple[str, bool]:
        if "sessionid=" not in cookie:
            raise InvalidSessionError("Cookie ini tidak punya sessionid")
        return "erenyaan", True

    monkeypatch.setattr(session_routes, "validate_instagram_cookie", fake_validate)
    app.state.session_factory = session_factory
    app.state.redis = fakeredis.FakeAsyncRedis()
    app.state.platform_sessions = sources
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        login = await http_client.post(
            "/api/auth/login", json={"username": "aliv", "password": PASSWORD}
        )
        assert login.status_code == 200
        yield http_client, {"instagram": instagram, "tiktok": tiktok, "budget": budget}


@requires_db
async def test_saving_a_cookie_switches_the_session_and_never_echoes_it(
    client: tuple[httpx.AsyncClient, dict[str, Any]],
) -> None:
    # Arrange
    http, parts = client
    parts["budget"].throttled("instagram", 900)

    # Act
    saved = await http.put("/api/platform-sessions/instagram", json={"cookie": IG_COOKIE})
    listing = await http.get("/api/platform-sessions")
    bad_tiktok = await http.put("/api/platform-sessions/tiktok", json={"cookie": "msToken=zzzzzz"})
    removed = await http.delete("/api/platform-sessions/instagram")

    # Assert
    assert saved.status_code == 200
    body = saved.json()
    assert (body["source"], body["accountName"], body["verified"]) == ("web", "erenyaan", True)
    assert body["waitSeconds"] == 0  # fresh accepted session drops the old cooldown
    assert "SECRET123" not in listing.text
    assert parts["instagram"].applied[-2].cookie == IG_COOKIE
    assert bad_tiktok.status_code == 422
    assert removed.json()["source"] in ("env", "none")
    assert parts["instagram"].applied[-1] is None


@requires_db
async def test_cookie_without_session_is_rejected(
    client: tuple[httpx.AsyncClient, dict[str, Any]],
) -> None:
    # Arrange
    http, _ = client

    # Act
    response = await http.put("/api/platform-sessions/instagram", json={"cookie": "csrftoken=abc"})

    # Assert
    assert response.status_code == 422
    assert "sessionid" in response.json()["detail"]


@requires_db
async def test_status_asks_to_replace_after_repeated_throttles_until_new_cookie(
    client: tuple[httpx.AsyncClient, dict[str, Any]],
) -> None:
    # Arrange
    http, parts = client
    parts["budget"].throttled("instagram", 900)
    parts["budget"].throttled("instagram", 900)

    # Act
    before = {s["platform"]: s for s in (await http.get("/api/platform-sessions")).json()}
    await http.put("/api/platform-sessions/instagram", json={"cookie": IG_COOKIE})
    after = {s["platform"]: s for s in (await http.get("/api/platform-sessions")).json()}

    # Assert
    assert "2 kali" in before["instagram"]["replaceReason"]
    assert before["tiktok"]["replaceReason"] is None
    assert after["instagram"]["replaceReason"] is None
