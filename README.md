# JaaJ Workspace

> **Early Development** — This project is under active development and has not reached a stable release. APIs, database schemas, and features may change without notice or migration path. Use at your own risk.

A self-hosted, modular productivity suite built with Django. Workspace brings together file management, dashboards, and user settings into a unified platform – with an extensible architecture designed for adding new modules (notes, tasks, calendar, and more).

## Features

### File Management
- Hierarchical folder tree with unlimited nesting
- Drag & drop file upload with visual overlay
- Built-in viewers for PDF, Markdown, images, video, audio, and code (with syntax highlighting)
- Favorites, pinned folders, and persistent sort preferences
- Soft-delete with trash and configurable retention (30 days)
- File copy with conflict resolution
- Custom folder icons and colors

### Dashboard
- Storage statistics and file/folder counts
- Recent files, favorites, and trash overview
- Module registry with quick access

### Unified Search
- Cross-module search via command palette
- Results grouped by type (files, folders, etc.)
- Extensible search provider system

### User Settings
- Per-user, per-module key-value settings store
- Password management with configurable validation rules
- Persistent dark/light theme toggle

## Tech Stack

| Layer              | Technology                                     |
|--------------------|------------------------------------------------|
| **Backend**        | Django 6.0, Django REST Framework, Celery      |
| **Frontend**       | Alpine.js, Tailwind CSS, DaisyUI, Lucide Icons |
| **Database**       | SQLite (WAL mode)                              |
| **Cache / Broker** | Redis (optional)                               |
| **Server**         | Gunicorn, WhiteNoise, Brotli compression       |
| **Tooling**        | uv, Docker, drf-spectacular (OpenAPI)          |

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (recommended package manager)
- Redis (optional, for caching and Celery)

## Getting Started

### 1. Clone the repository

```bash
git clone <repository-url>
cd Workspace
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Run migrations

```bash
python manage.py migrate
```

### 4. Create a superuser

```bash
python manage.py createsuperuser
```

### 5. Start the development server

```bash
python manage.py runserver
```

The application is available at `http://localhost:8000`.

## Docker

Build and run with Docker:

```bash
docker build -t workspace .
docker run -p 8000:8000 \
  -e SECRET_KEY=your-secret-key \
  -e DEBUG=0 \
  -e ALLOWED_HOSTS=localhost \
  workspace
```

### Environment Variables

| Variable           | Description                   | Default               |
|--------------------|-------------------------------|-----------------------|
| `SECRET_KEY`       | Django secret key             | *required*            |
| `DEBUG`            | Enable debug mode             | `True`                |
| `ALLOWED_HOSTS`    | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `SQLITE_PATH`      | Path to SQLite database       | `db.sqlite3`          |
| `REDIS_URL`        | Redis connection URL          | *(none)*              |
| `GUNICORN_WORKERS` | Number of Gunicorn workers    | `3`                   |
| `DJANGO_LOG_LEVEL` | Logging level                 | `INFO`                |

## Project Structure

```
Workspace/
├── workspace/               # Django project package
│   ├── settings.py          # Main configuration
│   ├── urls.py              # URL routing
│   ├── core/                # Module registry & unified search
│   ├── files/               # File management module
│   │   └── ui/              # File browser views & templates
│   ├── dashboard/           # Dashboard & insights
│   ├── users/               # Authentication & user settings
│   └── common/              # Shared templates & utilities
├── templates/               # Root-level templates
├── files/                   # File storage directory
├── manage.py
├── pyproject.toml
├── Dockerfile
└── IDEAS.md                 # Feature roadmap
```

## API

All endpoints are prefixed with `/api/v1/` and use no trailing slashes.

### Documentation

Interactive API documentation is available when the server is running:

- **Swagger UI** — `/api/v1/schema/swagger-ui/`
- **ReDoc** — `/api/v1/schema/redoc/`
- **OpenAPI Schema** — `/api/v1/schema/`

### Key Endpoints

| Endpoint                                         | Description                        |
|--------------------------------------------------|------------------------------------|
| `GET/POST /api/v1/files`                         | List or create files/folders       |
| `GET/PATCH/DELETE /api/v1/files/{uuid}`          | Retrieve, update, or delete a file |
| `GET /api/v1/files/{uuid}/content`               | Download file content              |
| `POST /api/v1/files/{uuid}/copy`                 | Copy a file or folder              |
| `POST /api/v1/files/{uuid}/favorite`             | Toggle favorite                    |
| `POST /api/v1/files/{uuid}/pin`                  | Pin/unpin folder                   |
| `GET /api/v1/files/trash`                        | List trashed items                 |
| `POST /api/v1/files/{uuid}/restore`              | Restore from trash                 |
| `GET /api/v1/modules`                            | List registered modules            |
| `GET /api/v1/search?q={query}`                   | Unified search                     |
| `GET /api/v1/users/me`                           | Current user profile               |
| `GET/PUT/DELETE /api/v1/settings/{module}/{key}` | User settings                      |

## Module System

Workspace uses a dynamic module registry. Each module registers itself on startup with metadata (name, icon, color, URL) and an optional search provider. The registry is accessible via the `/api/v1/modules` endpoint and powers the sidebar navigation and unified search.

Adding a new module involves:

1. Creating a Django app under `workspace/`
2. Registering a `ModuleInfo` in the app's `AppConfig.ready()` method
3. Optionally registering a search provider for unified search

## Health Checks

Health check endpoints are available at `/health/` for orchestration and monitoring tools.

## Roadmap

See [IDEAS.md](IDEAS.md) for the full feature roadmap, including planned modules:

- Notes & Wiki
- Tasks & Projects
- Email Client
- Calendar & Scheduling
- Contacts & CRM
- Chat & Messaging
- And more

## License

All rights reserved.
