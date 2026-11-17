"""Account management. Usage inside the backend container:

python -m app.cli create-user <username>
python -m app.cli set-password <username>
python -m app.cli ig-session --out /tmp/secrets/ig-session
python -m app.cli import-folders /data/import --category internasional
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

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


def _drive_lost_message(exc: BaseException) -> str:
    return (
        f"Folder import tidak bisa dibaca lagi ({exc}). Di Windows biasanya sambungan Docker ke "
        "drive D: putus. Jalankan: docker compose down, wsl --shutdown, buka lagi Docker Desktop, "
        "docker compose up -d, lalu ulangi perintah ini. File yang sudah masuk tidak akan dobel."
    )


async def _import_folders(root: Path, category: str) -> None:
    from app.factory import build_storage
    from app.library import folder_import

    try:
        exists = root.is_dir()
    except OSError as exc:
        sys.exit(_drive_lost_message(exc))
    if not exists:
        sys.exit(f"Folder {root} tidak ada. Copy folder-foldernya ke data/import dulu")
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        report = await folder_import.import_folders(
            root,
            "internasional" if category == "internasional" else "lokal",
            build_session_factory(engine),
            build_storage(settings),
            on_file=lambda progress: print(f"\r{progress}    ", end="", flush=True),
        )
    except folder_import.ImportInterrupted as exc:
        print()
        sys.exit(_drive_lost_message(exc.__cause__ or exc))
    finally:
        await engine.dispose()
    print()
    for folder in report.folders:
        print(
            f"{folder.name}: {folder.converted} diubah ke MP4, {folder.saved} masuk, "
            f"{folder.skipped} sudah ada, "
            f"{folder.failed} gagal, {folder.ignored} bukan foto/video"
        )
    if report.loose_files:
        print(f"{report.loose_files} file langsung di {root} dilewati: taruh di dalam subfolder")


def _ig_session(out_path: str) -> None:
    """Build an instaloader session from browser cookies, skipping IG's login checkpoint."""
    import instaloader

    raw = getpass.getpass("Paste header Cookie dari instagram.com (tidak tampil di layar): ")
    cookies = dict(
        part.strip().split("=", 1) for part in raw.split(";") if "=" in part and part.strip()
    )
    if "sessionid" not in cookies:
        sys.exit("Cookie tidak punya sessionid. Pastikan login dulu di instagram.com")

    loader = instaloader.Instaloader(quiet=True)
    loader.load_session("", cookies)
    username = loader.test_login()
    if not username:
        sys.exit("Session ditolak Instagram. Ambil cookie baru dari browser yang sedang login")
    loader.context.username = username
    loader.save_session_to_file(out_path)
    print(f"Session {username} disimpan ke {out_path}. Isi IG_USERNAME={username} di backend/.env")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create-user", "set-password"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("ig-session").add_argument("--out", default="/tmp/secrets/ig-session")
    importer = sub.add_parser("import-folders")
    importer.add_argument("root", type=Path)
    importer.add_argument("--category", choices=["lokal", "internasional"], default="lokal")
    args = parser.parse_args()

    if args.command == "import-folders":
        asyncio.run(_import_folders(args.root, args.category))
        return

    if args.command == "ig-session":
        _ig_session(args.out)
        return

    password = _prompt_password()
    if args.command == "create-user":
        asyncio.run(_create_user(args.username, password))
    else:
        asyncio.run(_set_password(args.username, password))


if __name__ == "__main__":
    main()
