from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import hash_password, verify_password
from app.db import User
from app.library.jobs import JobRunner
from app.main import app
from tests.conftest import requires_db
from tests.test_library import PHOTO_URL_BLUE, FakeCdnStorage, FakeResolver, no_sleep

pytestmark = requires_db

PASSWORD = "correct horse battery"


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    async def enqueue_job(self, name: str, job_id: str, **kwargs: Any) -> None:
        self.jobs.append(job_id)


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> AsyncIterator[httpx.AsyncClient]:
    async with session_factory() as db:
        db.add(User(username="aliv", password_hash=hash_password(PASSWORD)))
        await db.commit()
    app.state.session_factory = session_factory
    app.state.redis = FakeRedis()
    app.state.queue = FakeQueue()
    app.state.storage = FakeCdnStorage(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def login(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"username": "aliv", "password": PASSWORD})
    assert response.status_code == 200


def test_password_hash_roundtrip() -> None:
    # Arrange
    stored = hash_password("s3cret password")

    # Act / Assert
    assert verify_password("s3cret password", stored)
    assert not verify_password("wrong", stored)
    assert stored != hash_password("s3cret password")


async def test_library_requires_login(client: httpx.AsyncClient) -> None:
    # Arrange / Act
    responses = [
        await client.get("/api/folders"),
        await client.post("/api/downloads", json={}),
        await client.delete("/api/folders/1"),
    ]

    # Assert
    assert [r.status_code for r in responses] == [401, 401, 401]


async def test_feed_stays_public(client: httpx.AsyncClient) -> None:
    # Arrange / Act
    response = await client.get("/api/health")

    # Assert
    assert response.status_code == 200


async def test_login_wrong_password_then_rate_limit(client: httpx.AsyncClient) -> None:
    # Arrange
    bad = {"username": "aliv", "password": "nope"}

    # Act
    statuses = [(await client.post("/api/auth/login", json=bad)).status_code for _ in range(11)]

    # Assert
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


async def test_login_me_logout(client: httpx.AsyncClient) -> None:
    # Arrange
    await login(client)

    # Act
    me = await client.get("/api/auth/me")
    logout = await client.post("/api/auth/logout")
    me_after = await client.get("/api/auth/me")

    # Assert
    assert me.json() == {"user": {"username": "aliv"}}
    assert logout.status_code == 204
    assert me_after.json() == {"user": None}


async def test_download_flow_list_serve_and_delete(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await login(client)
    response = await client.post(
        "/api/downloads",
        json={
            "folderName": "Bali",
            "platform": "instagram",
            "username": "@someone",
            "postIds": ["p1", "p1"],
        },
    )
    assert response.status_code == 202
    job = response.json()
    assert job["totalPosts"] == 1
    assert app.state.queue.jobs == [job["id"]]
    runner = JobRunner(
        session_factory,
        {"instagram": FakeResolver({"p1": [PHOTO_URL_BLUE]})},
        app.state.storage,
        {},
        10_000_000,
        no_sleep,
    )

    # Act
    await runner.run(job["id"])
    job_status = (await client.get(f"/api/jobs/{job['id']}")).json()
    folders = (await client.get("/api/folders")).json()
    folder_id = folders[0]["id"]
    photos = (await client.get(f"/api/folders/{folder_id}/items?type=photo")).json()
    videos = (await client.get(f"/api/folders/{folder_id}/items?type=video")).json()
    file_response = await client.get(photos[0]["fileUrl"], headers={"Range": "bytes=0-9"})
    thumb_response = await client.get(photos[0]["thumbnailUrl"])
    delete_item = await client.delete(f"/api/items/{photos[0]['id']}")
    delete_folder = await client.delete(f"/api/folders/{folder_id}")

    # Assert
    assert (job_status["status"], job_status["savedItems"]) == ("done", 1)
    assert [(f["name"], f["photoCount"], f["videoCount"]) for f in folders] == [("Bali", 1, 0)]
    assert len(photos) == 1 and videos == []
    assert file_response.status_code == 206
    assert len(file_response.content) == 10
    assert thumb_response.headers["content-type"] == "image/webp"
    assert (delete_item.status_code, delete_folder.status_code) == (204, 204)
    assert (await client.get("/api/folders")).json() == []
    assert list((app.state.storage.root / "blobs").rglob("*.jpg")) == []


async def test_download_rejects_blank_folder_and_bad_post_id(client: httpx.AsyncClient) -> None:
    # Arrange
    await login(client)
    base = {"platform": "tiktok", "username": "someone"}

    # Act
    blank = await client.post(
        "/api/downloads", json={**base, "folderName": "   ", "postIds": ["1"]}
    )
    bad_id = await client.post(
        "/api/downloads", json={**base, "folderName": "x", "postIds": ["../etc"]}
    )

    # Assert
    assert (blank.status_code, bad_id.status_code) == (422, 422)
