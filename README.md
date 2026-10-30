# Personal Feed Story

Feed viewer for TikTok and Instagram accounts, with a private download library. React frontend (Vite) plus a FastAPI backend that scrapes on demand and keeps the result for a month, scraping again only when the account changed.

- Anyone can view a feed.
- After login (one account, created from the CLI) you can tick posts and stories, download them into a named folder, and browse the folder with Photo and Video tabs. Folders and single files can be deleted.
- Posts already in any folder are greyed out in the feed and cannot be ticked again.
- "Tambah file" uploads your own photos and videos into a folder (drag and drop or file picker). The tab is picked from the file extension.
- ZIP and RAR files can be added the same way (max `MAX_ARCHIVE_BYTES`, 2 GB, and 4 GB unpacked). The worker unpacks them with `bsdtar`, puts every photo and video inside (any subfolder) into the folder, skips other files, and refuses password protected, split, or old RAR4 solid archives with a clear message.
- Feed cards are carousels (all slides of an Instagram carousel or a TikTok slideshow) with arrows, dots and autoplay that pauses on hover, focus, video playback and off screen.
- "Muat lebih banyak" loads older posts. Instagram pages by cursor, TikTok by 30 day windows back to one year.
- Instagram Reels load on demand, 6 per page with "Muat reels lagi" for the rest. Each page costs about 8 requests (one per reel), so pages are cached for a month like the feed, and Reels pause once 60% of the Instagram budget is used, leaving the rest for feeds and downloads. Downloads reuse the URLs from the Reels page.
- Instagram highlights load on demand. Story and highlight items share ids, so a downloaded item is greyed out wherever it shows up.
- Folders have Post, Story and Upload tabs, each split into Foto and Video.
- Instagram public profiles and posts are fetched logged out first (budget `INSTAGRAM_ANON_RATE_BUDGET`, counted per server IP), so they do not use the account. Private, age or region restricted profiles fall back to the session. Stories, highlights and reels always use the session. A logged out rate limit never puts the account on hold.
- Instagram stories load only when "Tampilkan story" is clicked (cached 10 minutes), the story query being the one Instagram watches most.
- Scraped pages (feed, older pages, reels, highlights) are kept in Postgres for 30 days. Opening a cached account shows the cache right away. At most once an hour per account a cheap check runs (1 logged out request for the post or video count). Only when that shows a change is the account scraped again, and its other cached pages are dropped. If the check or the scrape is refused (rate limit), the cache is shown. "Scrape ulang" always scrapes. Stories older than 24 hours are hidden from cached pages.
- Photos and covers are saved to `./data/library/mediacache` the first time they are shown, so a month old page still renders after the CDN links expire. Videos stream from the CDN: when a cached video link expired, playing it asks for a new link once (one platform request). The worker deletes expired pages and media files older than 35 days every night.
- Favorites (login required): "Favoritkan" on an open feed pins the account to a row above the search form, with its profile photo. A blue ring means a new post (TikTok and Instagram) or a new Instagram story since you last opened that feed. The worker checks TikTok favorites every 2 hours and Instagram ones every 6 hours (post count only, logged out), "Cek sekarang" checks all right away.
- A request budget per platform (`INSTAGRAM_RATE_BUDGET`, `TIKTOK_RATE_BUDGET`, default 100 and 150 requests per 15 minutes) warns in the UI when close to the limit and refuses with "tunggu sekitar N menit" when used up or after a real 429. These numbers are our own conservative guesses.

## Architecture

```
browser -> vite dev server (/api proxy) -> backend (FastAPI :8000)
                                             |-> douk (DouK-Downloader Web API :5555, internal only) -> TikTok
                                             |-> instaloader (in process) -> Instagram
                                             |-> redis (login rate limit, request budget, job queue)
                                             |-> postgres (users, sessions, folders, media items, jobs, page cache)
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

Windows PowerShell:

```powershell
copy backend\.env.example backend\.env
mkdir backend\secrets, douk\volume -Force
copy douk\settings.example.json douk\volume\settings.json
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

Easiest: open the **Session** page in the web app (after login), paste the `cookie` request header from a logged in instagram.com tab and click "Simpan dan cek". The page has a step by step guide for Instagram and TikTok. A cookie saved there is encrypted with a key in `./data/secrets` (created on first use), replaces the `.env` / session file value, and reaches the backend and the worker within 10 seconds, no restart. A cookie Instagram accepts also clears the app's own cooldown. The CLI below still works.


Use a secondary Instagram account. Logging in from the container usually triggers an Instagram checkpoint, so reuse the session of a browser that is already logged in instead. Copy the `cookie` request header from instagram.com (DevTools > Network > the `www.instagram.com` document request > Request Headers), then run this and paste it (input is hidden):

```bash
docker compose run --rm --no-deps -v ./backend/secrets:/tmp/secrets backend python -m app.cli ig-session --out /tmp/secrets/ig-session
```

It checks the session with Instagram and prints the username to put in `.env`. Logging in with a password also works when there is no checkpoint: `... backend instaloader --login YOUR_IG_USERNAME --sessionfile /tmp/secrets/ig-session`.

Then set `IG_USERNAME=YOUR_IG_USERNAME` in `backend/.env` and run `docker compose up -d backend worker`.

Backend API docs: http://127.0.0.1:8000/docs

## Daily use

Setup above is a one time thing. After that, all commands run from the repo root.

Start:

```bash
docker compose up -d
npm run dev
```

Open http://localhost:5173. `npm run dev` keeps the terminal busy, leave it open.

Stop:

```bash
# Ctrl+C in the npm run dev terminal, then
docker compose down
```

`docker compose down` keeps the data: Postgres lives in the `pgdata` volume and files in `./data/library`. Never add `-v`, that deletes the database.

After `git pull`:

1. Stop `npm run dev` first, so Vite does not serve half updated files.
2. Run `git pull`.
3. If anything under `backend/` changed, run `docker compose up -d --build backend worker`.
4. If `package.json` changed, run `npm install`.
5. Run `npm run dev`, then hard refresh the browser with Ctrl+Shift+R.

Check what is running and read logs:

```bash
docker compose ps
docker compose logs backend --tail 50
docker compose logs worker --tail 50
```

After editing `backend/.env` (new cookie, `IG_USERNAME`):

```bash
docker compose up -d backend worker
```

`up -d` recreates the containers with the new env. `restart` does not reload `.env`.

### Troubleshooting

| Symptom | Fix |
|---|---|
| "Backend mati atau error", or Vite logs `ECONNREFUSED` on `/api` | The stack is not up. Run `docker compose up -d` and check `docker compose ps`. |
| TikTok answers 403 | `TIKTOK_COOKIE` expired or is empty. Copy a fresh `Cookie` request header (not `Set-Cookie`) into `backend/.env`, then `docker compose up -d backend worker`. |
| Instagram stories missing, or "login" errors | The IG session expired. Repeat step 5. |
| Feed shows "(dari cache)" with old data | The hourly check found no change. Click "Scrape ulang" to force a scrape. |
| Covers or photos stop loading | They were never shown before their CDN link expired. Click "Scrape ulang". |
| "tunggu sekitar N menit" | The request budget is used up. Wait, scraping again too soon risks the account. |
| Blank page or `does not provide an export named` after a pull | Stop Vite, run `npm run dev -- --force`, hard refresh. |
| UI change not visible after a pull | Same as above. Check `git log --oneline -1` matches GitHub. |

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

- `GET /api/instagram/reels?username=<name>&cursor=&refresh=false`

Login required:

| Endpoint | Purpose |
|---|---|
| `POST /api/downloads` | `{folderName, platform, username, postIds}` creates a job, max `MAX_POSTS_PER_JOB` posts |
| `GET /api/jobs/{id}` | Progress: done, failed, saved, skipped, errors |
| `GET /api/folders`, `GET /api/folders/{id}` | Folders with photo and video counts and size |
| `GET /api/folders/{id}/items?type=photo\|video` | Items in a folder |
| `GET /api/items/{id}/file`, `/thumbnail` | File (supports Range for video seeking, `?download=true` for attachment) |
| `DELETE /api/folders/{id}`, `DELETE /api/items/{id}` | Delete |
| `POST /api/folders/{id}/files` | Upload one file (multipart field `file`). Same file twice in one folder is skipped |
| `POST /api/downloaded/lookup` | `{platform, postIds}` returns the folders that already hold each post |
| `GET /api/favorites` | Favorites with avatar, `updates` (`post`, `story`) and whether a check is running |
| `POST /api/favorites` | `{platform, username}` adds an account and probes it once for the avatar |
| `DELETE /api/favorites/{id}` | Remove |
| `POST /api/favorites/{id}/seen` | Clears the blue ring, called when the feed is opened |
| `POST /api/favorites/check` | Queues a check of every favorite now |

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
- CDN media links are signed and expire. Photos are kept on disk once shown, videos get a new link when played.
- Download jobs run one at a time. Instagram waits 4 to 9 seconds between posts by default (`INSTAGRAM_DELAY_SECONDS`), TikTok 1 to 3 (`TIKTOK_DELAY_SECONDS`). A rate limit stops the rest of the job.
- Story download needs the Instagram session, and stories expire after 24 hours.
- Instagram downloads reuse the media URLs cached in Redis when the feed, story or highlight was shown (12 hours), so a download sends no extra Instagram queries. Older items, or links that expired, are looked up again. An Instagram `feedback_required` answer means the account is being limited: the app stops Instagram for 30 minutes.
- Favorite checks compare the post count from the profile lookup (1 request per account, plus 1 for Instagram stories). A deleted post can hide a new one. Background checks stop once 70% of a platform budget is used, so manual scraping keeps some room.
- `COMPRESS_PHOTOS` (WebP) and `TRANSCODE_VIDEOS` (H.265) are off by default and only apply to new downloads. H.265 does not play in Firefox.
- Scraping goes against the TikTok and Instagram terms of service. Use secondary accounts and keep request volume low.

## VPS notes (later)

- Put the app behind a reverse proxy with https and set `SESSION_COOKIE_SECURE=true`. `/api/feed` is public and scrapes with your cookies, so consider rate limiting it at the proxy.
- Set `POSTGRES_PASSWORD` in a root `.env` file next to `docker-compose.yml`.
- `./data/library` must be writable by uid 10001: `sudo chown -R 10001 data/library`.
- The backend container runs as uid 10001. Make the session file readable: `chmod 640 backend/secrets/ig-session && sudo chown 10001 backend/secrets/ig-session`.
- Build the frontend with `npm run build` and serve `dist/` from the same origin as `/api`, or set `CORS_ORIGINS`.
