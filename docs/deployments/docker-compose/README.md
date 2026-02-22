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
| `GUNICORN_WORKERS`     | `6`                              | Number of Gunicorn workers                |

### Example `.env`

```env
SECRET_KEY=your-very-secret-random-key
ALLOWED_HOSTS=workspace.example.com
CSRF_TRUSTED_ORIGINS=https://workspace.example.com
GUNICORN_WORKERS=4
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

## Limitations

- **SQLite** is not designed for high-concurrency writes. WAL mode is enabled for better concurrent read performance, but for heavy workloads consider switching to PostgreSQL.
- **No Redis**: Celery uses in-memory broker (`memory://`), which means task state is lost on restart. For production, add a Redis service and set `REDIS_URL`.
