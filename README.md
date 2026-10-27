# Personal Feed Story

Feed viewer for TikTok and Instagram accounts, with a private download library. React frontend (Vite) plus a FastAPI backend that scrapes on demand and caches the result in Redis.

- Anyone can view a feed.
- After login (one account, created from the CLI) you can tick posts and stories, download them into a named folder, and browse the folder with Photo and Video tabs. Folders and single files can be deleted.

## Architecture

```
browser -> vite dev server (/api proxy) -> backend (FastAPI :8000)
                                             |-> douk (DouK-Downloader Web API :5555, internal only) -> TikTok
                                             |-> instaloader (in process) -> Instagram
                                             |-> redis (feed cache, login rate limit, job queue)
                                             |-> postgres (users, sessions, folders, media items, jobs)
worker (arq) <- redis queue
  |-> douk / instaloader: resolve original media URLs per post, random delay between posts
  |-> ./data/library: files stored once per sha256, thumbnails, optional webp and h265
```

| Part | Source | Notes |
|---|---|---|
| TikTok | [DouK-Downloader](https://github.com/JoeanAmier/TikTokDownloader) 5.8, built by `douk/Dockerfile` from the git tag | Needs a TikTok cookie. Username is resolved to `secUid` through `POST /tiktok/user`, a route `douk/user_route.py` adds to DouK (profile HTML scraping gets a WAF challenge). You can also pass a `secUid` (starts with `MS4wLjABAAAA`) directly as the username. |
| Instagram | [instaloader](https://github.com/instaloader/instaloader) | Posts work without login but the rate limit is tight. Stories need a session file. |
| Library | `backend/app/library/` | Files live in `./data/library/blobs/<sha[:2]>/<sha>.<ext>`. The same bytes downloaded into two folders are stored once. Deleting a folder or file removes a blob only when nothing else points at it. |
| Media | `GET /api/media?url=` | Proxies covers because the IG and TikTok CDNs block hotlinking. Only CDN hosts on the allowlist in `backend/app/media.py` are accepted. |

DouK has no auth, so its port is never published. Only the backend talks to it over the compose network.

## Run locally

Requirements: Docker with Compose, Node 20+.

### 1. Configure secrets

```bash
cp backend/.env.example backend/.env
mkdir -p backend/secrets douk/volume
cp douk/settings.example.json douk/volume/settings.json
```

Fill `TIKTOK_COOKIE` in `backend/.env` (DevTools > Network > any request to tiktok.com > `Cookie` header). Use a secondary account.

Leave `IG_USERNAME` empty until you set up the Instagram session (step 5).

### 2. Accept the DouK disclaimer once

DouK asks for a disclaimer confirmation on first run and stores the answer in `douk/volume`. Run it interactively once, type `YES`, wait until it says the API is up, then stop it with Ctrl+C:

```bash
docker compose run --rm douk
```

`settings.json` sets `run_command` to `7`, which makes DouK go straight into Web API mode on every later start.

### 3. Start the stack

```bash
docker compose up -d --build
npm install
npm run dev
```

Open http://localhost:5173, pick a platform, type a username.

### 4. Create the login account

There is no sign up page. Create the one account from the CLI (password at least 10 characters):

```bash
docker compose exec backend python -m app.cli create-user aliv
```

Change it later with `python -m app.cli set-password aliv`, which also logs out every existing session.

### 5. Instagram session (needed for stories)

Log in once with a secondary Instagram account. The session file is written to `backend/secrets/ig-session`:

```bash
docker compose run --rm --no-deps -v ./backend/secrets:/tmp/secrets backend instaloader --login YOUR_IG_USERNAME --sessionfile /tmp/secrets/ig-session
```

Then set `IG_USERNAME=YOUR_IG_USERNAME` in `backend/.env` and run `docker compose up -d backend worker`.

Backend API docs: http://127.0.0.1:8000/docs

## Backend development

```bash
cd backend
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests
```

Library and auth tests need a Postgres database they can wipe. They are skipped when `TEST_DATABASE_URL` is not set:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://feed:feed@127.0.0.1:5432/feed_test uv run pytest
```

Schema changes go through Alembic: edit `app/db.py`, then `uv run alembic revision --autogenerate -m "..."` and review the file. The backend container runs `alembic upgrade head` on start.

## API

Public:

- `GET /api/feed?platform=tiktok|instagram&username=<name>&limit=12&refresh=false`
- `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` (returns `{"user": null}` when logged out)

Login required:

| Endpoint | Purpose |
|---|---|
| `POST /api/downloads` | `{folderName, platform, username, postIds}` creates a job, max `MAX_POSTS_PER_JOB` posts |
| `GET /api/jobs/{id}` | Progress: done, failed, saved, skipped, errors |
| `GET /api/folders`, `GET /api/folders/{id}` | Folders with photo and video counts and size |
| `GET /api/folders/{id}/items?type=photo\|video` | Items in a folder |
| `GET /api/items/{id}/file`, `/thumbnail` | File (supports Range for video seeking, `?download=true` for attachment) |
| `DELETE /api/folders/{id}`, `DELETE /api/items/{id}` | Delete |

Errors come back as `{"detail": "..."}`:

| Status | Meaning |
|---|---|
| 404 | Account not found |
| 403 | Private account, no posts in the TikTok lookback window, or expired cookie |
| 429 | Instagram rate limit |
| 502 | DouK, TikTok, or Instagram unreachable |

If Redis is down the API keeps working without cache and logs a warning.

## Known limits

- TikTok only returns posts from the last `TIKTOK_LOOKBACK_DAYS` days (default 30). DouK pages the account until it passes that date, so a bigger window means more requests.
- TikTok stories are not supported by DouK. Only Instagram fills the Stories row.
- Instagram has no share count, so the share stat is hidden there.
- CDN media links are signed and expire. If covers stop loading, use "Scrape ulang" to bypass the cache.
- Download jobs run one at a time. Instagram waits 4 to 9 seconds between posts by default (`INSTAGRAM_DELAY_SECONDS`), TikTok 1 to 3 (`TIKTOK_DELAY_SECONDS`). A rate limit stops the rest of the job.
- Story download needs the Instagram session, and stories expire after 24 hours.
- `COMPRESS_PHOTOS` (WebP) and `TRANSCODE_VIDEOS` (H.265) are off by default and only apply to new downloads. H.265 does not play in Firefox.
- Scraping goes against the TikTok and Instagram terms of service. Use secondary accounts and keep request volume low.

## VPS notes (later)

- Put the app behind a reverse proxy with https and set `SESSION_COOKIE_SECURE=true`. `/api/feed` is public and scrapes with your cookies, so consider rate limiting it at the proxy.
- Set `POSTGRES_PASSWORD` in a root `.env` file next to `docker-compose.yml`.
- `./data/library` must be writable by uid 10001: `sudo chown -R 10001 data/library`.
- The backend container runs as uid 10001. Make the session file readable: `chmod 640 backend/secrets/ig-session && sudo chown 10001 backend/secrets/ig-session`.
- Build the frontend with `npm run build` and serve `dist/` from the same origin as `/api`, or set `CORS_ORIGINS`.
