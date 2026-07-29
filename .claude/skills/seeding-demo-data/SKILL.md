---
name: seeding-demo-data
description: Use when a populated local database is needed - testing through the web UI or Playwright, needing login credentials for /login, demoing the app, reproducing a bug against realistic data, or preparing a disposable environment. Also use when wondering what username/password to sign in with after seeding.
---

# Seeding Demo Data

## Overview

`scripts/seed_demo.py` fills the database with realistic demo data (users, file trees, chat, calendars, kanban projects with tasks, avatars) and always creates a deterministic login on top of the faker users.

**Credentials: username `demo`, password `demo1234`, at `/login`.**

The login form authenticates by USERNAME, never by email (stock Django `ModelBackend`, no custom backend). Typing `demo@demo.local` fails; type `demo`.

## Quick start (uses the dev DB `db.sqlite3` at repo root)

```powershell
uv run python manage.py migrate
uv run python scripts/seed_demo.py --users 10 --seed 42
uv run python manage.py runserver   # http://localhost:8000/login -> demo / demo1234
```

No Redis or Celery worker is needed: without `REDIS_URL` the app falls back to LocMem cache + DB sessions, and `DEBUG` (default `True`) makes Celery tasks run eagerly in-process.

## Fast minimal seed (agents, quick checks)

```powershell
uv run python scripts/seed_demo.py --users 3 --min-files 2 --max-files 5 --min-messages 2 --max-messages 5 --min-events 1 --max-events 3 --min-tasks 2 --max-tasks 4 --seed 42
```

## Disposable environment (do not touch the dev DB)

File blobs land in `MEDIA_ROOT`, which defaults to the repo root, so redirect BOTH the DB and the media directory:

```powershell
$tmp = "$env:TEMP\workspace-demo"
New-Item -ItemType Directory -Force $tmp | Out-Null
$env:DATABASE_URL = "sqlite:///$($tmp -replace '\\','/')/db.sqlite3"
$env:MEDIA_ROOT = $tmp
uv run python manage.py migrate
uv run python scripts/seed_demo.py --users 3 --seed 42
uv run python manage.py runserver
```

Env vars do not persist across shell tool calls: chain migrate/seed/runserver in the same call, or re-set the vars in each call.

## Flag reference

| Flag | Effect |
|---|---|
| `--seed 42` | Reproducible run |
| `--users N` | N faker users (the `demo` user is always added on top) |
| `--purge --yes` | Wipe all `@demo.local` users + their data/blobs first |
| `--no-files` / `--no-chat` / `--no-calendar` / `--no-projects` | Skip a generator |
| `--min-tasks N` / `--max-tasks N` | Tasks per shared project (defaults 6 / 30) |
| `--no-avatars` | Faster, skips Pillow avatar generation |
| `--history-days N` | Spread activity over the last N days (default 180) |
| `--password X` | Change the shared password (default `demo1234`) |

## Gotchas

- Re-running is additive: each run adds a new batch of faker users. Use `--purge --yes` to reset. The `demo` user is reused (its password is reset to `--password`), never duplicated.
- The `demo` user gets files, calendars and conversations like everyone else, so its account looks lived-in. It is also the admin of the first shared kanban project and has its own personal board.
- The seeder does not create a superuser. For `/admin`: `$env:DJANGO_SUPERUSER_PASSWORD='admin1234'; uv run python manage.py createsuperuser --noinput --username admin --email admin@demo.local`.
- `scripts/_screenshot_seed.py` is NOT for this: it feeds `scripts/screenshots.py` into a temporary DB that is deleted after the run.
- Seeding uses the service layer directly (no HTTP), so no server needs to be running while seeding.
