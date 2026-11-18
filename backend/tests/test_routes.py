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

    async def enqueue_job(self, name: str, job_id: str, *args: Any, **kwargs: Any) -> None:
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
    assert first.json()["item"]["addedAt"]
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


async def test_rotation_is_saved_per_item_and_validated(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await login(client)
    folder_id = await _create_folder(client, session_factory)
    upload = await client.post(
        f"/api/folders/{folder_id}/files",
        files={"file": ("tidur.jpg", _jpeg_bytes("blue"), "image/jpeg")},
    )
    item_id = upload.json()["item"]["id"]

    # Act
    rotated = await client.put(f"/api/items/{item_id}/rotation", json={"rotation": 90})
    listed = (await client.get(f"/api/folders/{folder_id}/items?type=photo&source=upload")).json()
    odd_angle = await client.put(f"/api/items/{item_id}/rotation", json={"rotation": 45})
    missing = await client.put("/api/items/999999/rotation", json={"rotation": 90})

    # Assert
    assert upload.json()["item"]["rotation"] == 0
    assert rotated.json()["rotation"] == 90
    assert listed[0]["rotation"] == 90
    assert (odd_angle.status_code, missing.status_code) == (422, 404)


async def test_folder_opens_by_slug_or_by_old_numeric_id(client: httpx.AsyncClient) -> None:
    # Arrange
    await login(client)
    created = (await client.post("/api/folders", json={"name": "Ayu Sastrani"})).json()

    # Act
    by_slug = await client.get("/api/folders/ayu-sastrani")
    by_id = await client.get(f"/api/folders/{created['id']}")
    listed = (await client.get("/api/folders")).json()
    unknown = await client.get("/api/folders/tidak-ada")

    # Assert
    assert created["slug"] == "ayu-sastrani"
    assert by_slug.json()["id"] == by_id.json()["id"] == created["id"]
    assert listed[0]["slug"] == "ayu-sastrani"
    assert unknown.status_code == 404


async def test_whole_folder_downloads_as_one_zip(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await login(client)
    folder_id = await _create_folder(client, session_factory)
    url = f"/api/folders/{folder_id}/files"
    await client.post(url, files={"file": ("a.jpg", _jpeg_bytes("green"), "image/jpeg")})
    await client.post(url, files={"file": ("b.jpg", _jpeg_bytes("red"), "image/jpeg")})
    empty = (await client.post("/api/folders", json={"name": "Kosong"})).json()

    # Act
    response = await client.get(f"/api/folders/{folder_id}/zip")
    empty_response = await client.get(f"/api/folders/{empty['id']}/zip")

    # Assert
    assert response.status_code == 200
    assert "Upload " in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["a.jpg", "b.jpg"]
    assert empty_response.status_code == 404


async def test_folder_category_defaults_to_lokal_and_can_change(client: httpx.AsyncClient) -> None:
    # Arrange
    await login(client)
    lokal = (await client.post("/api/folders", json={"name": "Raneey"})).json()
    luar = (
        await client.post("/api/folders", json={"name": "Lisa", "category": "internasional"})
    ).json()

    # Act
    moved = await client.put(
        f"/api/folders/{lokal['id']}/category", json={"category": "internasional"}
    )
    invalid = await client.put(f"/api/folders/{lokal['id']}/category", json={"category": "asia"})
    history = (await client.get("/api/activity")).json()["events"]

    # Assert
    assert (lokal["category"], luar["category"]) == ("lokal", "internasional")
    assert moved.json()["category"] == "internasional"
    assert invalid.status_code == 422
    assert history[0]["action"] == "set_category"
    assert history[0]["detail"] == "ke Internasional"


async def test_subtitles_generate_once_then_translate_and_serve_lines(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    from app.library.subtitles import Line, SubtitleFiles, SubtitleStatus, Transcript
    from tests.test_subtitles import make_video

    await login(client)
    files = SubtitleFiles(tmp_path / "subs")
    app.state.subtitle_files = files
    app.state.subtitle_status = SubtitleStatus(fakeredis.FakeRedis())
    folder_id = await _create_folder(client, session_factory)
    video = make_video(tmp_path / "talk.mp4", seconds=2).read_bytes()
    photo = _jpeg_bytes("red")
    url = f"/api/folders/{folder_id}/files"
    video_id = (await client.post(url, files={"file": ("talk.mp4", video, "video/mp4")})).json()[
        "item"
    ]["id"]
    photo_id = (await client.post(url, files={"file": ("p.jpg", photo, "image/jpeg")})).json()[
        "item"
    ]["id"]
    base = f"/api/items/{video_id}/subtitles"

    # Act
    before = await client.get(base)
    early_translation = await client.post(f"{base}/en")
    generated = await client.post(base)
    generated_again = await client.post(base)
    sha = app.state.queue.jobs[-1]
    files.save(sha, "original", Transcript("id", [Line(0.0, 1.5, "halo semua")]))
    translation = await client.post(f"{base}/en")
    lines = await client.get(f"{base}/original")
    missing = await client.get(f"{base}/id")
    on_photo = await client.post(f"/api/items/{photo_id}/subtitles")

    # Assert
    assert [t["state"] for t in before.json()["tracks"]] == ["none", "none", "none"]
    assert early_translation.status_code == 409
    assert generated.status_code == 202
    assert generated.json()["tracks"][0]["state"] == "queued"
    assert generated_again.json()["tracks"][0]["state"] == "queued"
    assert app.state.queue.jobs.count(sha) == 2  # one transcription, one translation
    assert translation.json()["language"] == "id"
    assert [t["state"] for t in translation.json()["tracks"]] == ["ready", "queued", "none"]
    assert lines.json()["lines"] == [{"start": 0.0, "end": 1.5, "text": "halo semua"}]
    assert missing.status_code == 404
    assert on_photo.status_code == 400


async def test_translation_can_be_redone_with_claude_only_when_its_key_is_set(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Arrange
    from app.library.subtitle_routes import get_claude_available
    from app.library.subtitles import Line, SubtitleFiles, SubtitleStatus, Transcript
    from tests.test_subtitles import make_video

    await login(client)
    files = SubtitleFiles(tmp_path / "subs")
    status = SubtitleStatus(fakeredis.FakeRedis())
    app.state.subtitle_files = files
    app.state.subtitle_status = status
    folder_id = await _create_folder(client, session_factory)
    video = make_video(tmp_path / "talk.mp4", seconds=2).read_bytes()
    item = (
        await client.post(
            f"/api/folders/{folder_id}/files", files={"file": ("t.mp4", video, "video/mp4")}
        )
    ).json()["item"]
    base = f"/api/items/{item['id']}/subtitles"
    await client.post(base)
    sha = app.state.queue.jobs[-1]
    files.save(sha, "original", Transcript("en", [Line(0, 1, "hi")]))
    files.save(sha, "id", Transcript("id", [Line(0, 1, "hai")], "groq"))

    # Act
    without_key = await client.post(f"{base}/id/claude")
    app.dependency_overrides[get_claude_available] = lambda: True
    try:
        redo = await client.post(f"{base}/id/claude")
        status.set(sha, "id", "failed", "Claude error 500")
        after_failure = await client.get(base)
    finally:
        app.dependency_overrides.pop(get_claude_available)

    # Assert
    assert without_key.status_code == 400
    assert redo.status_code == 202
    assert redo.json()["claudeAvailable"] is True
    redo_id = next(t for t in redo.json()["tracks"] if t["track"] == "id")
    assert (redo_id["state"], redo_id["translator"]) == ("queued", "groq")
    failed_id = next(t for t in after_failure.json()["tracks"] if t["track"] == "id")
    assert (failed_id["state"], failed_id["error"]) == ("ready", "Claude error 500")
