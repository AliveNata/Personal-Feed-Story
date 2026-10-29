"""Session page API: paste a platform cookie, see which session is in use, or remove it."""

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from app.auth import require_user
from app.config import get_settings
from app.errors import SourceError
from app.factory import Sources
from app.models import CamelModel, Platform
from app.platform_sessions import InvalidSessionError, parse_cookie_header
from app.sources.instagram import validate_instagram_cookie

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/platform-sessions", tags=["sessions"], dependencies=[Depends(require_user)]
)

PLATFORMS: tuple[Platform, ...] = ("instagram", "tiktok")


def get_platform_sources(request: Request) -> Sources:
    sources: Sources = request.app.state.platform_sessions
    return sources


AppSources = Annotated[Sources, Depends(get_platform_sources)]


class SessionStatus(CamelModel):
    platform: Platform
    # web: pasted on the Session page, env: from .env or the session file, none: nothing
    source: Literal["web", "env", "none"]
    account_name: str | None
    verified: bool
    updated_at: datetime | None
    # Seconds the app still holds requests to this platform after a "slow down".
    wait_seconds: int


class SaveSessionRequest(CamelModel):
    cookie: str = Field(min_length=10, max_length=20_000)


def _env_source(platform: Platform) -> Literal["env", "none"]:
    settings = get_settings()
    if platform == "tiktok":
        return "env" if settings.tiktok_cookie.get_secret_value() else "none"
    has_file = settings.ig_session_file is not None and settings.ig_session_file.exists()
    return "env" if settings.ig_username and has_file else "none"


async def _status(sources: Sources, platform: Platform) -> SessionStatus:
    assert sources.sessions is not None
    stored = await sources.sessions.get(platform)
    wait = (await asyncio.to_thread(sources.budget.status, platform)).wait_seconds
    if stored is not None:
        return SessionStatus(
            platform=platform,
            source="web",
            account_name=stored.account_name or None,
            verified=stored.verified,
            updated_at=stored.updated_at,
            wait_seconds=wait,
        )
    source = _env_source(platform)
    settings = get_settings()
    return SessionStatus(
        platform=platform,
        source=source,
        account_name=(settings.ig_username or None) if platform == "instagram" else None,
        verified=False,
        updated_at=None,
        wait_seconds=wait,
    )


@router.get("", response_model=list[SessionStatus])
async def list_sessions(sources: AppSources) -> list[SessionStatus]:
    return [await _status(sources, platform) for platform in PLATFORMS]


@router.put("/{platform}", response_model=SessionStatus)
async def save_session(
    platform: Platform, body: SaveSessionRequest, sources: AppSources
) -> SessionStatus:
    assert sources.sessions is not None and sources.syncs is not None
    cookie = " ".join(body.cookie.split()).removeprefix("Cookie:").removeprefix("cookie:").strip()
    if not parse_cookie_header(cookie):
        raise HTTPException(status_code=422, detail="Isinya bukan header Cookie (nama=nilai; ...)")

    try:
        if platform == "instagram":
            account, verified = await asyncio.to_thread(validate_instagram_cookie, cookie)
        else:
            # A TikTok cookie does not say whose it is, so there is no account name to show.
            account, verified = "", await sources.tiktok.check_cookie(cookie)
            if not verified:
                raise InvalidSessionError(
                    "TikTok menolak cookie ini. Ambil header Cookie dari tab tiktok.com yang login"
                )
    except InvalidSessionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await sources.sessions.save(platform, cookie, account, verified)
    await sources.syncs[platform].ensure_current(force=True)
    if verified:
        # A session the platform just accepted is not the one it was throttling.
        await asyncio.to_thread(sources.budget.clear_throttle, platform)
    logger.info("%s session saved from the web for %s (verified=%s)", platform, account, verified)
    return await _status(sources, platform)


@router.delete("/{platform}", response_model=SessionStatus)
async def delete_session(platform: Platform, sources: AppSources) -> SessionStatus:
    assert sources.sessions is not None and sources.syncs is not None
    await sources.sessions.delete(platform)
    await sources.syncs[platform].ensure_current(force=True)
    return await _status(sources, platform)
