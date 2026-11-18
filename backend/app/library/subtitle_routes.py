from typing import Annotated, Literal

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_db, require_user
from app.db import MediaItem
from app.library.routes import get_queue, get_storage
from app.library.storage import LibraryStorage
from app.library.subtitles import (
    TRANSLATION_TARGETS,
    SubtitleFiles,
    SubtitleStatus,
    Track,
    TranslationTarget,
)
from app.models import CamelModel

router = APIRouter(prefix="/api", tags=["subtitles"], dependencies=[Depends(require_user)])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Queue = Annotated[ArqRedis, Depends(get_queue)]
Storage = Annotated[LibraryStorage, Depends(get_storage)]


def get_subtitle_files(request: Request) -> SubtitleFiles:
    files: SubtitleFiles = request.app.state.subtitle_files
    return files


def get_subtitle_status(request: Request) -> SubtitleStatus:
    status: SubtitleStatus = request.app.state.subtitle_status
    return status


Files = Annotated[SubtitleFiles, Depends(get_subtitle_files)]
Status = Annotated[SubtitleStatus, Depends(get_subtitle_status)]
TRACKS: tuple[Track, ...] = ("original", *TRANSLATION_TARGETS)


class TrackState(CamelModel):
    track: Track
    state: Literal["none", "queued", "running", "failed", "ready"]
    error: str | None = None
    # 0 to 99 while running, None before the worker picks the job up.
    progress: int | None = None


class SubtitleInfo(CamelModel):
    # ISO 639-1 code of the spoken language, known once the original track is ready.
    language: str | None
    tracks: list[TrackState]


class SubtitleLine(CamelModel):
    start: float
    end: float
    text: str


class SubtitleTrack(CamelModel):
    track: Track
    language: str | None
    lines: list[SubtitleLine]


async def _video(db: AsyncSession, item_id: int) -> MediaItem:
    item = await db.get(MediaItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    if item.media_type != "video":
        raise HTTPException(status_code=400, detail="Subtitle cuma buat video")
    return item


def _info(sha256: str, files: SubtitleFiles, status: SubtitleStatus) -> SubtitleInfo:
    tracks: list[TrackState] = []
    for track in TRACKS:
        if files.has(sha256, track):
            tracks.append(TrackState(track=track, state="ready"))
            continue
        raw = status.get(sha256, track)
        if raw is None:
            tracks.append(TrackState(track=track, state="none"))
        else:
            tracks.append(
                TrackState(
                    track=track,
                    state=raw["state"],
                    error=raw.get("error"),
                    progress=raw.get("progress"),
                )
            )
    original = files.load(sha256, "original")
    return SubtitleInfo(language=original.language if original else None, tracks=tracks)


@router.get("/items/{item_id}/subtitles", response_model=SubtitleInfo)
async def get_subtitles(item_id: int, db: DbSession, files: Files, status: Status) -> SubtitleInfo:
    item = await _video(db, item_id)
    return _info(item.blob_sha256, files, status)


def _is_pending(status: SubtitleStatus, sha256: str, track: Track) -> bool:
    raw = status.get(sha256, track)
    return raw is not None and raw["state"] in ("queued", "running")


@router.post("/items/{item_id}/subtitles", response_model=SubtitleInfo, status_code=202)
async def generate_subtitles(
    item_id: int, db: DbSession, files: Files, status: Status, queue: Queue, storage: Storage
) -> SubtitleInfo:
    item = await _video(db, item_id)
    sha = item.blob_sha256
    if not files.has(sha, "original") and not _is_pending(status, sha, "original"):
        video = storage.blob_path(sha, item.blob.extension)
        if not video.exists():
            raise HTTPException(status_code=410, detail="File hilang dari disk")
        status.set(sha, "original", "queued")
        await queue.enqueue_job(
            "transcribe_subtitles", sha, str(video), _job_id=f"subtitle-{sha}-original"
        )
    return _info(sha, files, status)


@router.post("/items/{item_id}/subtitles/{target}", response_model=SubtitleInfo, status_code=202)
async def translate_subtitles(
    item_id: int,
    target: TranslationTarget,
    db: DbSession,
    files: Files,
    status: Status,
    queue: Queue,
) -> SubtitleInfo:
    item = await _video(db, item_id)
    sha = item.blob_sha256
    if not files.has(sha, "original"):
        raise HTTPException(status_code=409, detail="Generate subtitle dulu")
    if not files.has(sha, target) and not _is_pending(status, sha, target):
        status.set(sha, target, "queued")
        await queue.enqueue_job(
            "translate_subtitles", sha, target, _job_id=f"subtitle-{sha}-{target}"
        )
    return _info(sha, files, status)


@router.get("/items/{item_id}/subtitles/{track}", response_model=SubtitleTrack)
async def get_subtitle_track(
    item_id: int, track: Track, db: DbSession, files: Files
) -> SubtitleTrack:
    item = await _video(db, item_id)
    transcript = files.load(item.blob_sha256, track)
    if transcript is None:
        raise HTTPException(status_code=404, detail="Subtitle ini belum ada")
    return SubtitleTrack(
        track=track,
        language=transcript.language,
        lines=[SubtitleLine(start=x.start, end=x.end, text=x.text) for x in transcript.lines],
    )
