import logging
from datetime import UTC, datetime
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_db, get_redis, require_user
from app.db import Favorite
from app.errors import SourceError
from app.favorites import service
from app.models import CamelModel, Platform, proxied_media_url
from app.sources import ProfileProber

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/favorites", tags=["favorites"], dependencies=[Depends(require_user)]
)

DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis)]


def get_probers(request: Request) -> dict[str, ProfileProber]:
    probers: dict[str, ProfileProber] = request.app.state.sources
    return probers


def get_queue(request: Request) -> ArqRedis:
    queue: ArqRedis = request.app.state.queue
    return queue


Probers = Annotated[dict[str, ProfileProber], Depends(get_probers)]


class FavoriteRequest(CamelModel):
    platform: Platform
    username: str = Field(pattern=r"^@?[A-Za-z0-9._-]{1,100}$")


class FavoriteResponse(CamelModel):
    id: int
    platform: Platform
    username: str
    avatar_url: str | None
    updates: list[service.UpdateKind]
    checked_at: datetime | None
    check_error: str | None


class FavoritesResponse(CamelModel):
    favorites: list[FavoriteResponse]
    checking: bool


class CheckResponse(CamelModel):
    checking: bool


def to_response(favorite: Favorite) -> FavoriteResponse:
    return FavoriteResponse(
        id=favorite.id,
        platform=favorite.platform,
        username=favorite.username,
        avatar_url=proxied_media_url(favorite.avatar_url or "") or None,
        updates=service.update_kinds(favorite),
        checked_at=favorite.checked_at,
        check_error=favorite.check_error,
    )


async def is_checking(redis: Redis) -> bool:
    try:
        return bool(await redis.exists(service.CHECKING_KEY))
    except RedisError as exc:
        logger.warning("could not read favorites check flag: %s", exc)
        return False


async def _load(db: AsyncSession, favorite_id: int) -> Favorite:
    favorite = await db.get(Favorite, favorite_id)
    if favorite is None:
        raise HTTPException(status_code=404, detail="Favorit tidak ditemukan")
    return favorite


@router.get("", response_model=FavoritesResponse)
async def list_favorites(db: DbSession, redis: RedisClient) -> FavoritesResponse:
    favorites = await service.list_favorites(db)
    return FavoritesResponse(
        favorites=[to_response(favorite) for favorite in favorites],
        checking=await is_checking(redis),
    )


@router.post("", response_model=FavoriteResponse)
async def add_favorite(body: FavoriteRequest, db: DbSession, probers: Probers) -> FavoriteResponse:
    username = body.username.removeprefix("@")
    existing = await service.get_favorite(db, body.platform, username)
    if existing is not None:
        return to_response(existing)

    favorite = Favorite(platform=body.platform, username=username)
    db.add(favorite)
    try:
        # Probe right away for the avatar and the "already seen" baseline. A failure here
        # keeps the favorite: the next background check fills it in.
        await service.probe_favorite(favorite, probers[body.platform])
    except SourceError as exc:
        logger.warning("first probe of %s/%s failed: %s", body.platform, username, exc)
    try:
        await db.commit()
    except IntegrityError:
        # Added twice at the same time.
        await db.rollback()
        existing = await service.get_favorite(db, body.platform, username)
        if existing is None:
            raise
        return to_response(existing)
    return to_response(favorite)


@router.delete("/{favorite_id}", status_code=204)
async def delete_favorite(favorite_id: int, db: DbSession) -> Response:
    await db.delete(await _load(db, favorite_id))
    await db.commit()
    return Response(status_code=204)


@router.post("/{favorite_id}/seen", response_model=FavoriteResponse)
async def mark_seen(favorite_id: int, db: DbSession) -> FavoriteResponse:
    favorite = await _load(db, favorite_id)
    service.mark_seen(favorite, datetime.now(UTC))
    await db.commit()
    return to_response(favorite)


@router.post("/check", response_model=CheckResponse, status_code=202)
async def check_now(
    redis: RedisClient, queue: Annotated[ArqRedis, Depends(get_queue)]
) -> CheckResponse:
    # Fixed job id: arq ignores the enqueue while the same check is queued or running.
    await queue.enqueue_job("check_favorites", True, _job_id=service.CHECK_JOB_ID)
    try:
        await redis.set(service.CHECKING_KEY, "1", ex=service.CHECKING_TTL_SECONDS)
    except RedisError as exc:
        logger.warning("could not set favorites check flag: %s", exc)
    return CheckResponse(checking=True)
