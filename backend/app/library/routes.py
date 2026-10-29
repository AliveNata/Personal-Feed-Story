from datetime import datetime
from typing import Annotated, Any, Literal

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field, field_validator
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_db, get_redis, require_user
from app.config import get_settings
from app.db import DownloadJob, Folder, MediaItem, User
from app.library import service
from app.library.progress import read_progress
from app.library.storage import DownloadError, LibraryStorage, content_type_for_upload
from app.models import CamelModel, Platform

POST_ID_PATTERN = r"^[A-Za-z0-9_-]{1,100}$"

router = APIRouter(prefix="/api", tags=["library"], dependencies=[Depends(require_user)])


def get_storage(request: Request) -> LibraryStorage:
    storage: LibraryStorage = request.app.state.storage
    return storage


def get_queue(request: Request) -> ArqRedis:
    queue: ArqRedis = request.app.state.queue
    return queue


DbSession = Annotated[AsyncSession, Depends(get_db)]
Storage = Annotated[LibraryStorage, Depends(get_storage)]


class DownloadRequest(CamelModel):
    folder_name: str = Field(min_length=1, max_length=100)
    platform: Platform
    username: str = Field(pattern=r"^@?[A-Za-z0-9._-]{1,100}$")
    post_ids: list[Annotated[str, Field(pattern=POST_ID_PATTERN)]] = Field(min_length=1)

    @field_validator("folder_name")
    @classmethod
    def folder_name_not_blank(cls, value: str) -> str:
        clean = service.normalize_folder_name(value)
        if not clean:
            raise ValueError("Nama folder tidak boleh kosong")
        return clean


class JobResponse(CamelModel):
    id: str
    folder_id: int
    status: str
    total_posts: int
    done_posts: int
    failed_posts: int
    saved_items: int
    skipped_items: int
    errors: list[dict[str, Any]]
    # Live, from Redis: the post being downloaded and how far, None between posts.
    current_post_id: str | None = None
    current_percent: int | None = None


class MediaCounts(CamelModel):
    photo: int
    video: int


class SourceCounts(CamelModel):
    post: MediaCounts
    story: MediaCounts
    upload: MediaCounts


class FolderResponse(CamelModel):
    id: int
    name: str
    photo_count: int
    video_count: int
    size_bytes: int
    created_at: datetime
    counts: SourceCounts
    # Random thumbnails for the folder row background, different on every load.
    preview_urls: list[str] = Field(default_factory=list)


class MediaItemResponse(CamelModel):
    id: int
    media_type: Literal["photo", "video"]
    platform: Literal["tiktok", "instagram", "upload"]
    author_username: str
    source_post_id: str
    item_index: int
    is_story: bool
    caption: str
    permalink: str | None
    posted_at: datetime | None
    size_bytes: int
    file_url: str
    thumbnail_url: str | None


def _job_response(job: DownloadJob) -> JobResponse:
    return JobResponse.model_validate(job, from_attributes=True)


def _folder_response(
    summary: service.FolderSummary, preview_ids: list[int] | None = None
) -> FolderResponse:
    return FolderResponse(
        id=summary.folder.id,
        name=summary.folder.name,
        photo_count=summary.photo_count,
        video_count=summary.video_count,
        size_bytes=summary.size_bytes,
        created_at=summary.folder.created_at,
        counts=SourceCounts.model_validate(summary.counts),
        preview_urls=[f"/api/items/{item_id}/thumbnail" for item_id in preview_ids or []],
    )


def _item_response(item: MediaItem) -> MediaItemResponse:
    return MediaItemResponse(
        id=item.id,
        media_type="video" if item.media_type == "video" else "photo",
        platform=item.platform,
        author_username=item.author_username,
        source_post_id=item.source_post_id,
        item_index=item.item_index,
        is_story=item.is_story,
        caption=item.caption,
        permalink=item.permalink,
        posted_at=item.posted_at,
        size_bytes=item.blob.size_bytes,
        file_url=f"/api/items/{item.id}/file",
        thumbnail_url=f"/api/items/{item.id}/thumbnail" if item.blob.has_thumbnail else None,
    )


@router.post("/downloads", response_model=JobResponse, status_code=202)
async def create_download(
    body: DownloadRequest,
    db: DbSession,
    queue: Annotated[ArqRedis, Depends(get_queue)],
) -> JobResponse:
    post_ids = list(dict.fromkeys(body.post_ids))
    limit = get_settings().max_posts_per_job
    if len(post_ids) > limit:
        raise HTTPException(status_code=422, detail=f"Maksimal {limit} post per download")

    folder = await service.get_or_create_folder(db, body.folder_name)
    job = await service.create_job(
        db, folder, body.platform, body.username.removeprefix("@"), post_ids
    )
    await db.commit()
    await queue.enqueue_job("download_job", job.id, _job_id=job.id)
    return _job_response(job)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str, db: DbSession, redis: Annotated[Redis, Depends(get_redis)]
) -> JobResponse:
    job = await db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    response = _job_response(job)
    if job.status == "running":
        response.current_post_id, response.current_percent = await read_progress(redis, job_id)
    return response


@router.get("/folders", response_model=list[FolderResponse])
async def list_folders(db: DbSession) -> list[FolderResponse]:
    previews = await service.random_previews(db)
    return [
        _folder_response(summary, previews.get(summary.folder.id))
        for summary in await service.list_folders(db)
    ]


@router.get("/folders/{folder_id}", response_model=FolderResponse)
async def get_folder(folder_id: int, db: DbSession) -> FolderResponse:
    summary = await service.get_folder_summary(db, folder_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Folder tidak ditemukan")
    return _folder_response(summary)


@router.get("/folders/{folder_id}/items", response_model=list[MediaItemResponse])
async def list_items(
    folder_id: int,
    db: DbSession,
    type: Literal["photo", "video"] = "photo",
    source: Literal["post", "story", "upload"] | None = None,
) -> list[MediaItemResponse]:
    if await db.get(Folder, folder_id) is None:
        raise HTTPException(status_code=404, detail="Folder tidak ditemukan")
    items = await service.list_media(db, folder_id, type, source)
    return [_item_response(item) for item in items]


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(folder_id: int, db: DbSession, storage: Storage) -> None:
    folder = await db.get(Folder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder tidak ditemukan")
    await service.delete_folder(db, storage, folder)


async def _get_item(db: AsyncSession, item_id: int) -> MediaItem:
    item = await db.get(MediaItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    return item


class MoveRequest(CamelModel):
    item_ids: list[int] = Field(min_length=1, max_length=500)
    folder_name: str = Field(min_length=1, max_length=100)

    @field_validator("folder_name")
    @classmethod
    def folder_name_not_blank(cls, value: str) -> str:
        clean = service.normalize_folder_name(value)
        if not clean:
            raise ValueError("Nama folder tidak boleh kosong")
        return clean


class MoveResponse(CamelModel):
    folder_id: int
    folder_name: str
    moved: int
    merged: int


@router.post("/items/move", response_model=MoveResponse)
async def move_items(body: MoveRequest, db: DbSession) -> MoveResponse:
    target = await service.get_or_create_folder(db, body.folder_name)
    result = await service.move_items(db, body.item_ids, target)
    return MoveResponse(
        folder_id=target.id, folder_name=target.name, moved=result.moved, merged=result.merged
    )


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: int, db: DbSession, storage: Storage) -> None:
    await service.delete_item(db, storage, await _get_item(db, item_id))


@router.get("/items/{item_id}/file")
async def get_item_file(
    item_id: int, db: DbSession, storage: Storage, download: bool = False
) -> FileResponse:
    item = await _get_item(db, item_id)
    path = storage.blob_path(item.blob_sha256, item.blob.extension)
    if not path.exists():
        raise HTTPException(status_code=410, detail="File hilang dari disk")
    filename = f"{item.platform}_{item.author_username}_{item.source_post_id}_{item.item_index}"
    return FileResponse(
        path,
        media_type=item.blob.content_type,
        filename=f"{filename}.{item.blob.extension}",
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/items/{item_id}/thumbnail")
async def get_item_thumbnail(item_id: int, db: DbSession, storage: Storage) -> FileResponse:
    item = await _get_item(db, item_id)
    path = storage.thumbnail_path(item.blob_sha256)
    if not item.blob.has_thumbnail or not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail tidak ada")
    return FileResponse(
        path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"}
    )


class UploadResponse(CamelModel):
    status: Literal["saved", "skipped"]
    item: MediaItemResponse | None


@router.post("/folders/{folder_id}/files", response_model=UploadResponse, status_code=201)
async def upload_file(
    folder_id: int,
    file: UploadFile,
    db: DbSession,
    storage: Storage,
    user: Annotated[User, Depends(require_user)],
) -> UploadResponse:
    if await db.get(Folder, folder_id) is None:
        raise HTTPException(status_code=404, detail="Folder tidak ditemukan")
    filename = file.filename or "file"
    content_type = content_type_for_upload(filename, file.content_type)
    if content_type is None:
        raise HTTPException(
            status_code=415, detail=f"{filename}: tipe file tidak didukung, hanya foto atau video"
        )
    try:
        uploaded = await storage.save_upload(
            file.read, content_type, get_settings().max_download_bytes
        )
    except DownloadError as exc:
        raise HTTPException(status_code=422, detail=f"{filename}: {exc}") from exc

    item = await service.add_file_to_folder(
        db,
        storage,
        folder_id,
        uploaded,
        service.ItemSource(
            platform="upload",
            author_username=user.username,
            # Keyed by content, so uploading the same file twice into a folder is a no-op.
            source_post_id=f"upload-{uploaded.sha256[:24]}",
            item_index=0,
            caption=filename,
        ),
    )
    if item is None:
        return UploadResponse(status="skipped", item=None)
    return UploadResponse(status="saved", item=_item_response(item))


class DownloadedLookupRequest(CamelModel):
    platform: Platform
    post_ids: list[Annotated[str, Field(pattern=POST_ID_PATTERN)]] = Field(max_length=200)


class DownloadedLookupResponse(CamelModel):
    # post id -> names of folders that already hold it
    folders_by_post: dict[str, list[str]]


@router.post("/downloaded/lookup", response_model=DownloadedLookupResponse)
async def lookup_downloaded(
    body: DownloadedLookupRequest, db: DbSession
) -> DownloadedLookupResponse:
    if not body.post_ids:
        return DownloadedLookupResponse(folders_by_post={})
    rows = await db.execute(
        select(MediaItem.source_post_id, Folder.name)
        .join(Folder, Folder.id == MediaItem.folder_id)
        .where(MediaItem.platform == body.platform, MediaItem.source_post_id.in_(body.post_ids))
        .distinct()
        .order_by(Folder.name)
    )
    folders_by_post: dict[str, list[str]] = {}
    for post_id, folder_name in rows.all():
        folders_by_post.setdefault(post_id, []).append(folder_name)
    return DownloadedLookupResponse(folders_by_post=folders_by_post)
