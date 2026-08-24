# Docker Compose Deployment

Simple single-node deployment using Docker Compose and SQLite.

## Architecture

```
┌──────────────┐  ┌───────────────┐  ┌──────────────┐
│     web      │  │ celery-worker │  │ celery-beat  │
│   gunicorn   │  │  background   │  │  scheduler   │
│    :8000     │  │    tasks      │  │              │
└──────┬───────┘  └──────┬────────┘  └──────┬───────┘
       │                 │                  │
       └─────────────────┼──────────────────┘
                         │
                    volume: data/
                  (SQLite + user files)
```

| Service           | Role                             |
|-------------------|----------------------------------|
| **web**           | Django/Gunicorn HTTP server      |
| **celery-worker** | Celery worker (background tasks) |
| **celery-beat**   | Celery beat (task scheduler)     |

## Prerequisites

- Docker and Docker Compose v2+
- Access to the built image or the Dockerfile at repository root

## Deploy

```bash
# 1. Copy docker-compose.yml to your server
#    Optionally create a .env file next to it

# 2. Build and start
docker compose up -d

# 3. Run migrations
docker compose exec web python manage.py migrate

# 4. Create an admin user
docker compose exec web python manage.py createsuperuser
```

## Configuration

All settings are configurable via environment variables or a `.env` file next to `docker-compose.yml`.

| Variable               | Default                          | Description                               |
|------------------------|----------------------------------|-------------------------------------------|
| `SECRET_KEY`           | `change-me-to-a-real-secret-key` | Django secret key. **Must be changed.**   |
| `ALLOWED_HOSTS`        | `*`                              | Comma-separated list of allowed hostnames |
| `CSRF_TRUSTED_ORIGINS` | *(empty)*                        | Comma-separated list of trusted origins   |
| `USE_X_FORWARDED_HOST` | *(empty)*                        | Set to `1` when the proxy rewrites `Host` (Cloudflare, cloud LBs) |
| `USE_X_FORWARDED_PORT` | *(empty)*                        | Set to `1` when the proxy rewrites the public port |
| `GUNICORN_WORKERS`     | `6`                              | Number of Gunicorn workers                |
| `WEBPUSH_VAPID_PRIVATE_KEY` | *(empty)*                   | VAPID private key. `manage.py generate_vapid_keys` prints raw base64url; PEM and base64url DER are also accepted |
| `WEBPUSH_VAPID_PUBLIC_KEY`  | *(empty)*                   | VAPID public key (base64url)              |
| `WEBPUSH_VAPID_MAILTO`      | *(empty)*                   | Contact email for VAPID claims (`mailto:…`) |
| `METRICS_USER`         | *(empty)*                        | HTTP Basic user for `/metrics`. Endpoint returns 401 to everyone until set |
| `METRICS_PASSWORD`     | *(empty)*                        | Matching password, in plain text. See [Monitoring](../../guides/monitoring.md) |
| `OAUTH_GOOGLE_CLIENT_ID` | *(empty)* | Google OAuth2 client ID (enables Gmail login) |
| `OAUTH_GOOGLE_CLIENT_SECRET` | *(empty)* | Google OAuth2 client secret |
| `OAUTH_MICROSOFT_CLIENT_ID` | *(empty)* | Microsoft OAuth2 client ID (enables Outlook login) |
| `OAUTH_MICROSOFT_CLIENT_SECRET` | *(empty)* | Microsoft OAuth2 client secret |
| `OAUTH_GENERIC_CLIENT_ID` | *(empty)* | Custom OAuth2 provider client ID |
| `OAUTH_GENERIC_CLIENT_SECRET` | *(empty)* | Custom OAuth2 provider client secret |
| `OAUTH_GENERIC_NAME` | *(empty)* | Display name for the custom provider (e.g. `Yahoo`) |
| `OAUTH_GENERIC_AUTH_URL` | *(empty)* | Authorization endpoint URL |
| `OAUTH_GENERIC_TOKEN_URL` | *(empty)* | Token endpoint URL |
| `OAUTH_GENERIC_SCOPES` | *(empty)* | Space-separated OAuth2 scopes |
| `OAUTH_GENERIC_IMAP_HOST` | *(empty)* | IMAP server hostname |
| `OAUTH_GENERIC_SMTP_HOST` | *(empty)* | SMTP server hostname |
| `OIDC_RP_CLIENT_ID` | *(empty)* | OIDC client ID. Set this plus the secret and the four endpoints to enable SSO login |
| `OIDC_RP_CLIENT_SECRET` | *(empty)* | OIDC client secret |
| `OIDC_OP_AUTHORIZATION_ENDPOINT` | *(empty)* | Provider authorization endpoint |
| `OIDC_OP_TOKEN_ENDPOINT` | *(empty)* | Provider token endpoint |
| `OIDC_OP_USER_ENDPOINT` | *(empty)* | Provider userinfo endpoint |
| `OIDC_OP_JWKS_ENDPOINT` | *(empty)* | Provider JWKS endpoint (required for RS256) |
| `OIDC_PROVIDER_NAME` | `OpenID` | Label shown on the login button |
| `OIDC_RP_SIGN_ALGO` | `RS256` | ID token signing algorithm |
| `OIDC_RP_SCOPES` | `openid email profile` | Requested scopes (`profile` provides the display name) |
| `OIDC_ALLOWED_DOMAINS` | *(empty)* | Comma-separated email-domain allowlist for sign-up |
| `OIDC_REQUIRE_EMAIL_VERIFIED` | *(empty)* | Set to `1` to require the `email_verified` claim |
| `OIDC_USERNAME_CLAIM` | `preferred_username` | Claim used for the Django username |
| `OIDC_GROUPS_CLAIM` | *(empty)* | Claim mirrored onto Django groups on each login (empty = no sync) |
| `OIDC_GROUPS_ALLOWED` | *(empty)* | Comma-separated allowlist of group names to mirror |
| `AI_API_KEY` | *(empty)* | OpenAI API key (or compatible provider). AI disabled if empty |
| `AI_BASE_URL` | *(empty)* | Custom base URL for the LLM API (Ollama, LM Studio, etc.) |
| `AI_MODEL` | `gpt-5` | Default LLM model for chat and tasks |
| `AI_MAX_TOKENS` | `2048` | Maximum tokens per AI response |
| `AI_CHAT_CONTEXT_SIZE` | `30` | Recent messages kept in full; older ones are summarized |
| `AI_TOOL_RESULT_STORE_MAX_CHARS` | `12000` | Characters of a tool result kept for the next turns; the newest turn is replayed in full |
| `AI_TOOL_RESULT_REPLAY_MIN_CHARS` | `1500` | Floor a replayed tool result decays to as its turn gets older |
| `AI_IMAGE_MODEL` | `dall-e-3` | Model for image generation |
| `AI_IMAGE_BASE_URL` | *(empty)* | Custom base URL for image generation (falls back to `AI_BASE_URL`) |
| `SEARXNG_URL` | *(empty)* | SearXNG instance URL for web search (e.g. `http://searxng:8080`) |
| `SEARXNG_BLOCKED_DOMAINS` | *(empty)* | Comma-separated list of domains the AI cannot fetch (e.g. `evil.com,spam.org`) |
| `WOPI_DISCOVERY_URL` | *(empty)* | WOPI editor discovery URL (e.g. `http://collabora:9980/hosting/discovery`). Empty = office files stay download-only |
| `WOPI_HOST_URL` | *(empty)* | Workspace origin *as reachable by the editor container* (e.g. `http://web:8000`). Empty = derived from the request |

### Example `.env`

```env
SECRET_KEY=your-very-secret-random-key
ALLOWED_HOSTS=workspace.example.com
CSRF_TRUSTED_ORIGINS=https://workspace.example.com
GUNICORN_WORKERS=4
WEBPUSH_VAPID_PRIVATE_KEY=_8O5gz...base64url...
WEBPUSH_VAPID_PUBLIC_KEY=BHh5Vu...base64url...
WEBPUSH_VAPID_MAILTO=mailto:admin@example.com

# Prometheus (optional) - /metrics stays closed until both are set
METRICS_USER=prometheus
METRICS_PASSWORD=a-long-random-password

# AI (optional)
AI_API_KEY=sk-your-openai-key
AI_MODEL=gpt-4o
AI_IMAGE_MODEL=dall-e-3
```

## Storage

A single Docker volume (`workspace-data`) is mounted at `/app/data` across all services and contains:

- The SQLite database (`db.sqlite3`)
- User-uploaded files and media

## Reverse Proxy

The web service exposes port **8000**. In production, place a reverse proxy (nginx, Caddy, Traefik) in front for TLS termination. When doing so, set:

```env
CSRF_TRUSTED_ORIGINS=https://your-domain.com
```

See [Reverse Proxy in the deployment overview](../README.md#reverse-proxy) for the headers the proxy must forward, optional `USE_X_FORWARDED_HOST/PORT` settings, and an important security warning when running without a proxy.

## Web Search (optional)

To give the AI web search capabilities, deploy a [SearXNG](https://docs.searxng.org/) instance alongside the stack:

1. Uncomment the `searxng` service in `docker-compose.yml`
2. Set `SEARXNG_URL=http://searxng:8080` in your `.env`
3. Restart: `docker compose up -d`

The `searxng/` directory contains a `settings.yml` that enables the JSON API format required by Workspace. It is mounted into the container automatically.

SearXNG requires no API keys - it aggregates results from public search engines.

## Office Documents (optional)

DOCX, XLSX and PPTX (and their ODF equivalents) open — and save — in the file viewer through a WOPI editor running next to the stack. Any WOPI editor works: [Collabora CODE](https://www.collaboraonline.com/code/) (free, the commented service in `docker-compose.yml`), OnlyOffice Docs in WOPI mode, or an on-premises Office Online Server. Without one, office files stay download-only.

1. Uncomment the `collabora` service in `docker-compose.yml` and put your public origin in `aliasgroup1` (dots regex-escaped, port included). The group lists every origin allowed to use the editor: the public one the browser sees, plus `http://web:8000` for the WOPI requests the editor makes over the compose network.
2. Set in your `.env`:

   ```env
   WOPI_DISCOVERY_URL=http://collabora:9980/hosting/discovery
   WOPI_HOST_URL=http://web:8000
   ```

3. Publish the editor through your reverse proxy: the browser loads it in an iframe, so it needs its own **public hostname with TLS** (e.g. `collabora.example.com` → `collabora:9980`, WebSocket upgrades included), and that hostname goes in `--o:server_name=...` — it is what the editor writes into the discovery XML and iframe URLs, so the app can fetch discovery internally while the browser still reaches the editor publicly. Collabora documents the exact nginx/Apache/Caddy blocks in its [proxy settings guide](https://sdk.collaboraonline.com/docs/installation/Proxy_settings.html), including serving the editor under a sub-path of the main domain if you prefer avoiding a second hostname.
4. Restart: `docker compose up -d`

Notes:

- The editor is memory-hungry (LibreOffice under the hood); give the host ~2 GB of headroom.
- Editing permissions follow file permissions: view-only shares open read-only, and saves go through the normal upload pipeline (thumbnails, events).
- If the editor is down, office files degrade to a download prompt — nothing else breaks.

## Limitations

- **SQLite** is not designed for high-concurrency writes. WAL mode is enabled for better concurrent read performance, but for heavy workloads consider switching to PostgreSQL.
- **No Redis**: Celery uses in-memory broker (`memory://`), which means task state is lost on restart. For production, add a Redis service and set `REDIS_URL`.
