import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import hash_password, verify_password
from app.db import User
from app.library.archive import ImportStore
from app.library.jobs import JobRunner
from app.main import app
from tests.conftest import requires_db
from tests.test_library import (
    PHOTO_URL_BLUE,
    PHOTO_URL_RED,
    FakeCdnStorage,
    FakeResolver,
    no_sleep,
    write_jpeg,
)

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


async def _create_folder(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> int:
    from app.library import service

    async with session_factory() as db:
        folder = await service.get_or_create_folder(db, "Upload")
        await db.commit()
        return folder.id


def _jpeg_bytes(color: str) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buffer, "JPEG")
    return buffer.getvalue()


async def test_upload_sorts_by_type_and_skips_duplicates(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await login(client)
    folder_id = await _create_folder(client, session_factory)
    photo = _jpeg_bytes("green")
    url = f"/api/folders/{folder_id}/files"

    # Act
    first = await client.post(url, files={"file": ("liburan.JPG", photo, "image/jpeg")})
    again = await client.post(url, files={"file": ("copy.jpg", photo, "image/jpeg")})
    fake_image = await client.post(
        url, files={"file": ("rusak.jpg", b"not an image", "image/jpeg")}
    )
    unsupported = await client.post(url, files={"file": ("notes.pdf", b"%PDF", "application/pdf")})
    photos = (await client.get(f"/api/folders/{folder_id}/items?type=photo")).json()

    # Assert
    assert first.status_code == 201
    assert first.json()["status"] == "saved"
    assert first.json()["item"]["platform"] == "upload"
    assert first.json()["item"]["caption"] == "liburan.JPG"
    assert again.json() == {"status": "skipped", "item": None}
    assert (fake_image.status_code, unsupported.status_code) == (422, 415)
    assert len(photos) == 1


async def test_upload_requires_login(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    folder_id = await _create_folder(client, session_factory)

    # Act
    response = await client.post(
        f"/api/folders/{folder_id}/files",
        files={"file": ("a.jpg", _jpeg_bytes("red"), "image/jpeg")},
    )

    # Assert
    assert response.status_code == 401


async def test_downloaded_lookup_lists_folders_per_post(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await login(client)
    for folder_name in ("B", "A"):
        job = (
            await client.post(
                "/api/downloads",
                json={
                    "folderName": folder_name,
                    "platform": "instagram",
                    "username": "x",
                    "postIds": ["p1"],
                },
            )
        ).json()
        await JobRunner(
            session_factory,
            {"instagram": FakeResolver({"p1": [PHOTO_URL_BLUE]})},
            app.state.storage,
            {},
            10_000_000,
            no_sleep,
        ).run(job["id"])

    # Act
    response = await client.post(
        "/api/downloaded/lookup", json={"platform": "instagram", "postIds": ["p1", "p2"]}
    )
    other_platform = await client.post(
        "/api/downloaded/lookup", json={"platform": "tiktok", "postIds": ["p1"]}
    )

    # Assert
    assert response.json() == {"foldersByPost": {"p1": ["A", "B"]}}
    assert other_platform.json() == {"foldersByPost": {}}


async def test_move_items_merges_duplicates_and_creates_target_folder(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange: Bali holds p1 and p2, Lombok already holds p1
    await login(client)
    resolver = FakeResolver({"p1": [PHOTO_URL_BLUE], "p2": [PHOTO_URL_RED]})
    runner = JobRunner(
        session_factory, {"instagram": resolver}, app.state.storage, {}, 10_000_000, no_sleep
    )
    for folder_name, post_ids in (("Bali", ["p1", "p2"]), ("Lombok", ["p1"])):
        job = (
            await client.post(
                "/api/downloads",
                json={
                    "folderName": folder_name,
                    "platform": "instagram",
                    "username": "someone",
                    "postIds": post_ids,
                },
            )
        ).json()
        await runner.run(job["id"])
    folders = {f["name"]: f["id"] for f in (await client.get("/api/folders")).json()}
    bali_items = (await client.get(f"/api/folders/{folders['Bali']}/items?type=photo")).json()
    ids = [item["id"] for item in bali_items]

    # Act
    merged = (
        await client.post("/api/items/move", json={"itemIds": ids, "folderName": "lombok"})
    ).json()
    lombok_items = (await client.get(f"/api/folders/{folders['Lombok']}/items?type=photo")).json()
    bali_after = (await client.get(f"/api/folders/{folders['Bali']}/items?type=photo")).json()
    created = (
        await client.post(
            "/api/items/move",
            json={"itemIds": [lombok_items[0]["id"]], "folderName": "  Folder   Baru "},
        )
    ).json()
    unauthorized = await httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ).post("/api/items/move", json={"itemIds": ids, "folderName": "x"})

    # Assert
    assert (merged["folderName"], merged["moved"], merged["merged"]) == ("Lombok", 1, 1)
    assert len(lombok_items) == 2
    assert bali_after == []
    assert (created["folderName"], created["moved"]) == ("Folder Baru", 1)
    assert unauthorized.status_code == 401


async def test_activity_records_download_move_upload_and_deletes(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await login(client)
    runner = JobRunner(
        session_factory,
        {"instagram": FakeResolver({"p1": [PHOTO_URL_BLUE], "p2": [PHOTO_URL_RED]})},
        app.state.storage,
        {},
        10_000_000,
        no_sleep,
    )
    job = (
        await client.post(
            "/api/downloads",
            json={
                "folderName": "Bali",
                "platform": "instagram",
                "username": "someone",
                "postIds": ["p1", "p2"],
            },
        )
    ).json()
    await runner.run(job["id"])
    bali = (await client.get("/api/folders")).json()[0]["id"]
    photos = (await client.get(f"/api/folders/{bali}/items?type=photo")).json()

    # Act
    await client.post("/api/items/move", json={"itemIds": [photos[0]["id"]], "folderName": "Salah"})
    jpeg = app.state.storage.root / "up.jpg"
    write_jpeg(jpeg, "red")
    for name in ("a.jpg", "b.jpg"):
        await client.post(
            f"/api/folders/{bali}/files", files={"file": (name, jpeg.read_bytes(), "image/jpeg")}
        )
    await client.delete(f"/api/items/{photos[1]['id']}")
    await client.delete(f"/api/folders/{bali}")
    everything = (await client.get("/api/activity")).json()
    page = (await client.get("/api/activity", params={"limit": 2})).json()
    salah = next(f for f in (await client.get("/api/folders")).json() if f["name"] == "Salah")
    for_salah = (await client.get("/api/activity", params={"folderId": salah["id"]})).json()

    # Assert
    actions = [(e["action"], e["itemCount"]) for e in everything["events"]]
    assert actions == [
        ("delete_folder", 1),
        ("delete_item", 1),
        ("upload", 1),
        ("move", 1),
        ("download", 2),
    ]
    move = everything["events"][3]
    assert (move["folderName"], move["targetFolderName"]) == ("Bali", "Salah")
    assert (move["folderExists"], move["targetFolderExists"]) == (False, True)
    assert len(page["events"]) == 2 and page["nextBefore"] is not None
    assert [e["action"] for e in for_salah["events"]] == ["move"]


async def test_archive_upload_queues_an_import_and_reports_its_state(
    client: httpx.AsyncClient,
) -> None:
    # Arrange
    await login(client)
    app.state.import_store = ImportStore(fakeredis.FakeRedis())
    folder_id = (await client.post("/api/folders/0/archives")).status_code  # 422: no file
    bali = (
        await client.post(
            "/api/downloads",
            json={
                "folderName": "Bali",
                "platform": "instagram",
                "username": "someone",
                "postIds": ["p1"],
            },
        )
    ).json()["folderId"]

    # Act
    queued = await client.post(
        f"/api/folders/{bali}/archives", files={"file": ("foto.zip", b"PK fake", "application/zip")}
    )
    wrong_type = await client.post(
        f"/api/folders/{bali}/archives", files={"file": ("foto.7z", b"x", "application/x-7z")}
    )
    state = await client.get(f"/api/archive-imports/{queued.json()['importId']}")

    # Assert
    assert folder_id == 422
    assert queued.status_code == 202
    assert app.state.queue.jobs[-1] == queued.json()["importId"]
    assert wrong_type.status_code == 415
    assert (state.json()["status"], state.json()["filename"]) == ("queued", "foto.zip")


async def test_create_folder_manually_and_refuse_duplicate_names(
    client: httpx.AsyncClient,
) -> None:
    # Arrange
    await login(client)

    # Act
    created = await client.post("/api/folders", json={"name": "  Liburan   Bali "})
    duplicate = await client.post("/api/folders", json={"name": "liburan bali"})
    blank = await client.post("/api/folders", json={"name": "   "})
    history = (await client.get("/api/activity")).json()["events"]

    # Assert
    assert created.status_code == 201
    assert (created.json()["name"], created.json()["photoCount"]) == ("Liburan Bali", 0)
    assert duplicate.status_code == 409
    assert "sudah ada" in duplicate.json()["detail"]
    assert blank.status_code == 422
    assert history[0]["action"] == "create_folder"


async def test_selected_files_download_as_one_zip_with_original_names(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await login(client)
    folder_id = await _create_folder(client, session_factory)
    url = f"/api/folders/{folder_id}/files"
    green, red = _jpeg_bytes("green"), _jpeg_bytes("red")
    first = (await client.post(url, files={"file": ("IMG_1.jpg", green, "image/jpeg")})).json()
    second = (await client.post(url, files={"file": ("IMG_1.jpg", red, "image/jpeg")})).json()
    ids = f"{first['item']['id']},{second['item']['id']},999999"

    # Act
    response = await client.get(f"/api/items/zip?ids={ids}&name=Liburan Bali")
    single = await client.get(f"/api/items/{first['item']['id']}/file?download=true")
    missing = await client.get("/api/items/zip?ids=999999")
    malformed = await client.get("/api/items/zip?ids=1;drop")

    # Assert
    assert response.status_code == 200
    assert 'filename="Liburan Bali.zip"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["IMG_1.jpg", "IMG_1 (2).jpg"]
        assert archive.read("IMG_1.jpg") == green
        assert archive.read("IMG_1 (2).jpg") == red
    assert 'filename="IMG_1.jpg"' in single.headers["content-disposition"]
    assert (missing.status_code, malformed.status_code) == (404, 422)
