# Personal Feed Story

Feed viewer for TikTok and Instagram accounts. React frontend (Vite) plus a FastAPI backend that scrapes on demand and caches the result in Redis.

## Architecture

```
browser -> vite dev server (/api proxy) -> backend (FastAPI :8000)
                                             |-> douk (DouK-Downloader Web API :5555, internal only) -> TikTok
                                             |-> instaloader (in process) -> Instagram
                                             |-> redis (feed cache, TTL 15 min)
```

| Part | Source | Notes |
|---|---|---|
| TikTok | [DouK-Downloader](https://github.com/JoeanAmier/TikTokDownloader) 5.8, built by `douk/Dockerfile` from the git tag | Needs a TikTok cookie. Username is resolved to `secUid` by reading the profile page. You can also pass a `secUid` (starts with `MS4wLjABAAAA`) directly as the username. |
| Instagram | [instaloader](https://github.com/instaloader/instaloader) | Posts work without login but the rate limit is tight. Stories need a session file. |
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

Instagram session (optional, needed for stories):

```bash
pip install instaloader
instaloader --login YOUR_IG_USERNAME --sessionfile backend/secrets/ig-session
```

Then set `IG_USERNAME` in `backend/.env`. If you skip this, leave `IG_USERNAME` empty.

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

Backend API docs: http://127.0.0.1:8000/docs

## Backend development

```bash
cd backend
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests
```

Run the backend outside Docker (DouK and Redis still in compose, expose them yourself if needed):

```bash
DOUK_BASE_URL=http://127.0.0.1:5555 REDIS_URL=redis://127.0.0.1:6379/0 uv run uvicorn app.main:app --reload
```

## API

`GET /api/feed?platform=tiktok|instagram&username=<name>&limit=12&refresh=false`

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
- Login, Dashboard, and "Save to Google Drive" are still mocks from the original template.
- Scraping goes against the TikTok and Instagram terms of service. Use secondary accounts and keep request volume low.

## VPS notes (later)

- Put the backend behind a reverse proxy with auth before exposing it. `/api/feed` triggers scraping with your cookies.
- The backend container runs as uid 10001. Make the session file readable: `chmod 640 backend/secrets/ig-session && sudo chown 10001 backend/secrets/ig-session`.
- Build the frontend with `npm run build` and serve `dist/` from the same origin as `/api`, or set `CORS_ORIGINS`.
