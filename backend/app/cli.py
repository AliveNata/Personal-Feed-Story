"""Account management. Usage inside the backend container:

python -m app.cli create-user <username>
python -m app.cli set-password <username>
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import delete, select

from app.auth import hash_password
from app.config import get_settings
from app.db import User, UserSession, build_engine, build_session_factory

MIN_PASSWORD_LENGTH = 10


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Password minimal {MIN_PASSWORD_LENGTH} karakter")
    if getpass.getpass("Ulangi password: ") != password:
        sys.exit("Password tidak sama")
    return password


async def _create_user(username: str, password: str) -> None:
    engine = build_engine(get_settings().database_url)
    try:
        async with build_session_factory(engine)() as db:
            if await db.scalar(select(User).where(User.username == username)):
                sys.exit(f"User {username} sudah ada, pakai set-password")
            db.add(User(username=username, password_hash=hash_password(password)))
            await db.commit()
    finally:
        await engine.dispose()
    print(f"User {username} dibuat")


async def _set_password(username: str, password: str) -> None:
    engine = build_engine(get_settings().database_url)
    try:
        async with build_session_factory(engine)() as db:
            user = await db.scalar(select(User).where(User.username == username))
            if user is None:
                sys.exit(f"User {username} tidak ditemukan")
            user.password_hash = hash_password(password)
            # A password change should kick out every existing login.
            await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
            await db.commit()
    finally:
        await engine.dispose()
    print(f"Password {username} diganti, semua sesi lama dihapus")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create-user", "set-password"):
        sub.add_parser(name).add_argument("username")
    args = parser.parse_args()

    password = _prompt_password()
    if args.command == "create-user":
        asyncio.run(_create_user(args.username, password))
    else:
        asyncio.run(_set_password(args.username, password))


if __name__ == "__main__":
    main()
