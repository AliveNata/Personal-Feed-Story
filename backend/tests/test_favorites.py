from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import hash_password
from app.db import Favorite, User
from app.errors import RateLimitedError, UserNotFoundError
from app.favorites import service
from app.main import app
from app.ratelimit import RateBudget
from app.sources import ProfileProbe
from tests.conftest import requires_db

PASSWORD = "correct horse battery"
NOW = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)


class FakeProber:
    def __init__(self, probe: ProfileProbe | Exception) -> None:
        self.result = probe
        self.calls: list[str] = []

    async def probe(self, username: str) -> ProfileProbe:
        self.calls.append(username)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, str | None]] = []

    async def enqueue_job(self, name: str, *args: Any, _job_id: str | None = None) -> None:
        self.jobs.append((name, _job_id))


async def no_sleep(_seconds: float) -> None:
    return None


def probe(posts: int, story_at: datetime | None = None) -> ProfileProbe:
    return ProfileProbe(
        avatar_url="https://scontent.cdninstagram.com/avatar.jpg",
        post_count=posts,
        latest_story_at=story_at,
    )


def make_budget(limit: int = 100) -> RateBudget:
    return RateBudget(fakeredis.FakeRedis(), {"instagram": (limit, 900), "tiktok": (limit, 900)})


def test_first_probe_counts_existing_posts_and_story_as_seen() -> None:
    # Arrange
    favorite = Favorite(platform="instagram", username="natgeo")

    # Act
    service.apply_probe(favorite, probe(posts=10, story_at=NOW), NOW)

    # Assert
    assert service.update_kinds(favorite) == []
    assert favorite.avatar_url == "https://scontent.cdninstagram.com/avatar.jpg"


def test_new_post_and_new_story_show_as_updates_until_seen() -> None:
    # Arrange
    favorite = Favorite(platform="instagram", username="natgeo")
    service.apply_probe(favorite, probe(posts=10, story_at=NOW), NOW)
    later = NOW + timedelta(hours=2)

    # Act
    service.apply_probe(favorite, probe(posts=11, story_at=later), later)
    before_seen = service.update_kinds(favorite)
    service.mark_seen(favorite, later + timedelta(minutes=5))

    # Assert
    assert before_seen == ["post", "story"]
    assert service.update_kinds(favorite) == []


def test_story_posted_before_visit_is_not_an_update_on_next_probe() -> None:
    # Arrange
    favorite = Favorite(platform="instagram", username="natgeo")
    service.apply_probe(favorite, probe(posts=10), NOW)
    story_at = NOW + timedelta(minutes=30)
    service.mark_seen(favorite, NOW + timedelta(hours=1))

    # Act
    service.apply_probe(favorite, probe(posts=10, story_at=story_at), NOW + timedelta(hours=2))

    # Assert
    assert service.update_kinds(favorite) == []


def test_deleted_post_is_not_an_update() -> None:
    # Arrange
    favorite = Favorite(platform="tiktok", username="someone")
    service.apply_probe(favorite, probe(posts=10), NOW)

    # Act
    service.apply_probe(favorite, probe(posts=9), NOW + timedelta(hours=2))

    # Assert
    assert service.update_kinds(favorite) == []


@requires_db
async def test_check_all_probes_and_stops_when_budget_is_reserved(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    async with session_factory() as db:
        db.add_all(
            [
                Favorite(platform="instagram", username="a", post_count=1, seen_post_count=1),
                Favorite(platform="tiktok", username="b", post_count=5, seen_post_count=5),
            ]
        )
        await db.commit()
    budget = make_budget(limit=10)
    budget.record("tiktok", 7)  # 7 + PROBE_COST is past 70% of 10
    instagram, tiktok = FakeProber(probe(posts=2)), FakeProber(probe(posts=6))

    # Act
    summary = await service.check_all(
        session_factory,
        {"instagram": instagram, "tiktok": tiktok},
        budget,
        {},
        sleep=no_sleep,
    )

    # Assert
    assert (summary.checked, summary.skipped_platforms) == (1, {"tiktok"})
    assert tiktok.calls == []
    async with session_factory() as db:
        favorites = {f.username: f for f in await service.list_favorites(db)}
    assert service.update_kinds(favorites["a"]) == ["post"]
    assert favorites["b"].checked_at is None


@requires_db
async def test_check_all_records_errors_and_stops_platform_on_rate_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    async with session_factory() as db:
        db.add_all(
            [
                Favorite(platform="instagram", username="gone"),
                Favorite(platform="tiktok", username="busy"),
            ]
        )
        await db.commit()
    probers = {
        "instagram": FakeProber(UserNotFoundError("Akun tidak ditemukan")),
        "tiktok": FakeProber(RateLimitedError("tunggu", retry_after=60)),
    }

    # Act
    summary = await service.check_all(session_factory, probers, make_budget(), {}, sleep=no_sleep)

    # Assert
    assert (summary.failed, summary.skipped_platforms) == (1, {"tiktok"})
    async with session_factory() as db:
        favorites = {f.username: f for f in await service.list_favorites(db)}
    assert favorites["gone"].check_error == "Akun tidak ditemukan"
    assert favorites["busy"].check_error is None


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    async with session_factory() as db:
        db.add(User(username="aliv", password_hash=hash_password(PASSWORD)))
        await db.commit()
    app.state.session_factory = session_factory
    app.state.redis = fakeredis.FakeAsyncRedis()
    app.state.queue = FakeQueue()
    app.state.sources = {
        "instagram": FakeProber(probe(posts=10)),
        "tiktok": FakeProber(UserNotFoundError("tidak ditemukan")),
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.post(
            "/api/auth/login", json={"username": "aliv", "password": PASSWORD}
        )
        assert response.status_code == 200
        yield http_client


@requires_db
async def test_favorites_require_login(session_factory: async_sessionmaker[AsyncSession]) -> None:
    # Arrange
    app.state.session_factory = session_factory
    transport = httpx.ASGITransport(app=app)

    # Act
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anonymous:
        response = await anonymous.get("/api/favorites")

    # Assert
    assert response.status_code == 401


@requires_db
async def test_add_favorite_probes_once_and_is_idempotent(client: httpx.AsyncClient) -> None:
    # Arrange
    body = {"platform": "instagram", "username": "@NatGeo"}

    # Act
    first = (await client.post("/api/favorites", json=body)).json()
    second = (await client.post("/api/favorites", json={**body, "username": "natgeo"})).json()
    listing = (await client.get("/api/favorites")).json()

    # Assert
    assert first["id"] == second["id"]
    assert first["username"] == "NatGeo"
    assert first["avatarUrl"].startswith("/api/media?url=")
    assert first["updates"] == []
    assert app.state.sources["instagram"].calls == ["NatGeo"]
    assert len(listing["favorites"]) == 1
    assert listing["checking"] is False


@requires_db
async def test_add_favorite_keeps_account_when_first_probe_fails(client: httpx.AsyncClient) -> None:
    # Arrange / Act
    response = await client.post("/api/favorites", json={"platform": "tiktok", "username": "x"})

    # Assert
    assert response.status_code == 200
    assert response.json()["checkError"] == "tidak ditemukan"
    assert response.json()["avatarUrl"] is None


@requires_db
async def test_seen_clears_update_and_delete_removes(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    favorite_id = (
        await client.post("/api/favorites", json={"platform": "instagram", "username": "natgeo"})
    ).json()["id"]
    async with session_factory() as db:
        favorite = await db.get(Favorite, favorite_id)
        assert favorite is not None
        favorite.post_count = 11
        await db.commit()

    # Act
    before = (await client.get("/api/favorites")).json()["favorites"][0]["updates"]
    seen = (await client.post(f"/api/favorites/{favorite_id}/seen")).json()["updates"]
    deleted = await client.delete(f"/api/favorites/{favorite_id}")
    after = (await client.get("/api/favorites")).json()["favorites"]

    # Assert
    assert (before, seen, deleted.status_code, after) == (["post"], [], 204, [])


@requires_db
async def test_check_now_enqueues_fixed_job_and_flags_checking(client: httpx.AsyncClient) -> None:
    # Arrange / Act
    response = await client.post("/api/favorites/check")
    listing = (await client.get("/api/favorites")).json()

    # Assert
    assert response.status_code == 202
    assert app.state.queue.jobs == [("check_favorites", service.CHECK_JOB_ID)]
    assert listing["checking"] is True


@requires_db
async def test_check_all_skips_recently_checked_platform_accounts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    recent = datetime.now(UTC) - timedelta(hours=1)
    async with session_factory() as db:
        db.add_all(
            [
                Favorite(
                    platform="instagram",
                    username="ig",
                    checked_at=recent,
                    post_count=1,
                    seen_post_count=1,
                ),
                Favorite(
                    platform="tiktok",
                    username="tt",
                    checked_at=recent,
                    post_count=1,
                    seen_post_count=1,
                ),
            ]
        )
        await db.commit()
    instagram, tiktok = FakeProber(probe(posts=2)), FakeProber(probe(posts=2))

    # Act
    await service.check_all(
        session_factory,
        {"instagram": instagram, "tiktok": tiktok},
        make_budget(),
        {},
        sleep=no_sleep,
        min_interval={"instagram": timedelta(hours=6)},
    )

    # Assert
    assert (instagram.calls, tiktok.calls) == ([], ["tt"])
