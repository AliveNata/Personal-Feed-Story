from typing import Annotated, Literal

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_db, require_user
from app.config import get_settings
from app.db import MediaItem
from app.library.routes import get_queue, get_storage
from app.library.storage import LibraryStorage
from app.library.subtitles import (
    TRANSLATION_TARGETS,
    SubtitleError,
    SubtitleFiles,
    SubtitleStatus,
    Track,
    Transcript,
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
    # "groq" or "claude" for a saved translation, so the menu knows whether Claude can redo it.
    translator: str | None = None


class SubtitleInfo(CamelModel):
    # ISO 639-1 code of the spoken language, known once the original track is ready.
    language: str | None
    tracks: list[TrackState]
    # ANTHROPIC_API_KEY is set: a Groq translation can be made more natural with Claude.
    claude_available: bool


class SubtitleLine(CamelModel):
    start: float
    end: float
    text: str


class SubtitleTrack(CamelModel):
    track: Track
    language: str | None
    lines: list[SubtitleLine]
    # Positions of lines changed by hand.
    edited: list[int]


class LineEdit(CamelModel):
    text: str = Field(min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Teks subtitle tidak boleh kosong")
        return clean


def _track_response(track: Track, transcript: Transcript) -> SubtitleTrack:
    return SubtitleTrack(
        track=track,
        language=transcript.language,
        lines=[SubtitleLine(start=x.start, end=x.end, text=x.text) for x in transcript.lines],
        edited=sorted(transcript.edited),
    )


async def _video(db: AsyncSession, item_id: int) -> MediaItem:
    item = await db.get(MediaItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    if item.media_type != "video":
        raise HTTPException(status_code=400, detail="Subtitle cuma buat video")
    return item


def _info(
    sha256: str, files: SubtitleFiles, status: SubtitleStatus, claude_available: bool
) -> SubtitleInfo:
    tracks: list[TrackState] = []
    for track in TRACKS:
        raw = status.get(sha256, track) or {}
        saved = files.load(sha256, track) if files.has(sha256, track) else None
        state = raw.get("state")
        # A translation being redone with Claude shows its progress over the saved file, a
        # failed redo leaves that file usable with the error beside it. The original is never
        # redone, so its file always wins.
        is_redoing = track != "original" and state in ("queued", "running")
        if saved is not None and not is_redoing:
            state = "ready"
        tracks.append(
            TrackState(
                track=track,
                state=state or "none",
                error=raw.get("error"),
                progress=raw.get("progress"),
                translator=saved.translator if saved else None,
            )
        )
    original = files.load(sha256, "original")
    return SubtitleInfo(
        language=original.language if original else None,
        tracks=tracks,
        claude_available=claude_available,
    )


def get_claude_available() -> bool:
    return bool(get_settings().anthropic_api_key.get_secret_value())


ClaudeAvailable = Annotated[bool, Depends(get_claude_available)]


@router.get("/items/{item_id}/subtitles", response_model=SubtitleInfo)
async def get_subtitles(
    item_id: int, db: DbSession, files: Files, status: Status, claude: ClaudeAvailable
) -> SubtitleInfo:
    item = await _video(db, item_id)
    return _info(item.blob_sha256, files, status, claude)


def _is_pending(status: SubtitleStatus, sha256: str, track: Track) -> bool:
    raw = status.get(sha256, track)
    return raw is not None and raw["state"] in ("queued", "running")


@router.post("/items/{item_id}/subtitles", response_model=SubtitleInfo, status_code=202)
async def generate_subtitles(
    item_id: int,
    db: DbSession,
    files: Files,
    status: Status,
    queue: Queue,
    storage: Storage,
    claude: ClaudeAvailable,
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
    return _info(sha, files, status, claude)


@router.post("/items/{item_id}/subtitles/{target}", response_model=SubtitleInfo, status_code=202)
async def translate_subtitles(
    item_id: int,
    target: TranslationTarget,
    db: DbSession,
    files: Files,
    status: Status,
    queue: Queue,
    claude: ClaudeAvailable,
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
    return _info(sha, files, status, claude)


async def _redo(
    item_id: int,
    target: TranslationTarget,
    use_claude: bool,
    db: AsyncSession,
    files: SubtitleFiles,
    status: SubtitleStatus,
    queue: ArqRedis,
    claude: bool,
) -> SubtitleInfo:
    item = await _video(db, item_id)
    sha = item.blob_sha256
    if use_claude and not claude:
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY belum diisi di backend/.env")
    if not files.has(sha, "original"):
        raise HTTPException(status_code=409, detail="Generate subtitle dulu")
    if not _is_pending(status, sha, target):
        status.set(sha, target, "queued")
        suffix = "claude" if use_claude else "redo"
        await queue.enqueue_job(
            "translate_subtitles",
            sha,
            target,
            use_claude,
            _job_id=f"subtitle-{sha}-{target}-{suffix}",
        )
    return _info(sha, files, status, claude)


@router.post(
    "/items/{item_id}/subtitles/{target}/claude", response_model=SubtitleInfo, status_code=202
)
async def retranslate_with_claude(
    item_id: int,
    target: TranslationTarget,
    db: DbSession,
    files: Files,
    status: Status,
    queue: Queue,
    claude: ClaudeAvailable,
) -> SubtitleInfo:
    """Translate again with Claude for more natural lines, replacing the earlier translation."""
    return await _redo(item_id, target, True, db, files, status, queue, claude)


@router.post(
    "/items/{item_id}/subtitles/{target}/redo", response_model=SubtitleInfo, status_code=202
)
async def retranslate(
    item_id: int,
    target: TranslationTarget,
    db: DbSession,
    files: Files,
    status: Status,
    queue: Queue,
    claude: ClaudeAvailable,
) -> SubtitleInfo:
    """Translate again with the default translator, for a translation that came out wrong."""
    return await _redo(item_id, target, False, db, files, status, queue, claude)


@router.get("/items/{item_id}/subtitles/{track}", response_model=SubtitleTrack)
async def get_subtitle_track(
    item_id: int, track: Track, db: DbSession, files: Files
) -> SubtitleTrack:
    item = await _video(db, item_id)
    transcript = files.load(item.blob_sha256, track)
    if transcript is None:
        raise HTTPException(status_code=404, detail="Subtitle ini belum ada")
    return _track_response(track, transcript)


@router.put("/items/{item_id}/subtitles/{track}/lines/{index}", response_model=SubtitleTrack)
async def edit_subtitle_line(
    item_id: int, track: Track, index: int, body: LineEdit, db: DbSession, files: Files
) -> SubtitleTrack:
    item = await _video(db, item_id)
    try:
        transcript = files.edit_line(item.blob_sha256, track, index, body.text)
    except SubtitleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _track_response(track, transcript)
