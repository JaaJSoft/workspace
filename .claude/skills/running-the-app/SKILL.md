---
name: running-the-app
description: Use when starting the local Django dev server (manage.py runserver) to preview, test, or screenshot the app, or when a port is already in use ("Error - That port is already in use", address already in use). Covers picking a port when several agent tasks run in parallel.
---

# Running the App (dev server)

## Overview

Several agent tasks often run in parallel on this machine (one git worktree each), and they all share localhost. A fixed port makes their dev servers collide. **Always start `runserver` on a fresh free port - never on the default 8000.**

## Start the server

Chain port-pick + runserver in ONE shell call (shell state does not persist across tool calls) and run that call in the background:

```powershell
$port = uv run python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])"
"PORT=$port"
uv run python manage.py runserver 127.0.0.1:$port --noreload
```

- `bind(('127.0.0.1', 0))` asks the OS for an unused port, so it never collides. Same idiom as `free_port()` in `scripts/screenshots.py`.
- `--noreload` keeps runserver single-process, so stopping the background task actually stops the server (the autoreloader child can outlive its parent on Windows).
- Read the `PORT=` line from the background task output; every later URL is `http://127.0.0.1:<port>/...`.
- Readiness check from a later call: `Invoke-WebRequest http://127.0.0.1:<port>/health/live -UseBasicParsing`.
- Need data and login credentials first? Use the seeding-demo-data skill and chain its migrate/seed commands before the lines above, in the same call.

## When a port is busy - THE RULE

**A busy port belongs to another live task's server. Never free a port by killing whatever holds it.** No `Stop-Process`, no `taskkill`, no `netstat`/`Get-NetTCPConnection` PID hunting. Pick another free port with the recipe above.

Only exception: stopping a server YOU started in THIS session, via the background task id or the PID you recorded at launch - never via a port lookup.

| Excuse | Reality |
|---|---|
| "The process on 8000 looks stale/orphaned" | It is another parallel task's live server. Killing it starts a mutual-kill loop between tasks. |
| "Docs/README say localhost:8000" | Single-developer default. Agent sessions must pick a free port. |
| "Freeing the port is faster" | The bind(0) recipe is one line and can never conflict. |
| "My own old server holds that port" | Stop it through the task id/PID you recorded, not a port lookup - the port may have been reused by someone else. |
