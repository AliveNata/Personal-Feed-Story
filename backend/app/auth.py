"""Single account login with server side sessions. Accounts are created with app.cli only."""

import hashlib
import hmac
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import User, UserSession, utc_now
from app.models import CamelModel

logger = logging.getLogger(__name__)

SESSION_COOKIE = "feed_session"
SESSION_TTL = timedelta(days=30)
LOGIN_ATTEMPT_LIMIT = 10
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60

# scrypt from the stdlib: memory hard, no extra dependency. n=2**14, r=8 is the common baseline.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        logger.error("password hash has an unexpected format")
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginRequest(CamelModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class UserResponse(CamelModel):
    username: str


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_session_factory(request)() as session:
        yield session


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


async def current_user(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = await db.get(UserSession, _token_hash(token))
    if session is None or session.expires_at <= utc_now():
        return None
    return session.user


async def require_user(user: Annotated[User | None, Depends(current_user)]) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Login dulu untuk akses fitur ini")
    return user


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> UserResponse:
    client_ip = request.client.host if request.client else "unknown"
    attempts_key = f"login_attempts:{client_ip}"
    attempts = await redis.incr(attempts_key)
    if attempts == 1:
        await redis.expire(attempts_key, LOGIN_ATTEMPT_WINDOW_SECONDS)
    if attempts > LOGIN_ATTEMPT_LIMIT:
        raise HTTPException(
            status_code=429, detail="Terlalu banyak percobaan login, tunggu 15 menit"
        )

    user = await db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        logger.warning("failed login for %s from %s", body.username, client_ip)
        raise HTTPException(status_code=401, detail="Username atau password salah")

    await redis.delete(attempts_key)
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=_token_hash(token), user_id=user.id, expires_at=utc_now() + SESSION_TTL
        )
    )
    await db.execute(delete(UserSession).where(UserSession.expires_at <= utc_now()))
    await db.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=get_settings().session_cookie_secure,
        path="/",
    )
    return UserResponse(username=user.username)


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, db: Annotated[AsyncSession, Depends(get_db)]
) -> None:
    if token := request.cookies.get(SESSION_COOKIE):
        await db.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(token)))
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


class SessionResponse(CamelModel):
    user: UserResponse | None


@router.get("/me", response_model=SessionResponse)
async def me(user: Annotated[User | None, Depends(current_user)]) -> SessionResponse:
    # 200 with user=null for guests: being logged out is a normal state, not an error.
    return SessionResponse(user=UserResponse(username=user.username) if user else None)
