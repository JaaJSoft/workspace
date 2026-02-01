# Workspace

> **Early Development** — This project is under active development and has not reached a stable release. APIs, database schemas, and features may change without notice or migration path. Use at your own risk.

A self-hosted, modular productivity suite built with Django. Workspace brings together file management, dashboards, and extensible modules into a unified platform you control.

**Run anywhere, scale everywhere** — from a Raspberry Pi to a Kubernetes cluster.

## Why Workspace?

Most productivity suites force you to choose: simple self-hosted tools that don't scale, or complex SaaS platforms you don't control.

Workspace gives you both: **run anywhere, scale everywhere**.

### Simplicity First
At its core, Workspace is just Django + SQLite. You can run it on a Raspberry Pi, a $5/month VPS, or your laptop. No complex setup, no database servers to manage, no infrastructure lock-in.

- **SQLite (WAL mode)** — Production-ready, zero-maintenance database. Perfect for individuals and small teams.
- **Single binary deployment** — One Docker container, one process, done.
- **Minimal dependencies** — Python, Django, and a file system. Redis is optional.

### Scale When You Need It
The same codebase that runs on SQLite can seamlessly switch to PostgreSQL for high-concurrency workloads or large datasets. The architecture supports multiple deployment strategies:

**Personal/Small Teams:**
- Run directly on a VPS with SQLite
- Docker Compose for simple multi-user setups
- Fly.io, Railway, or any PaaS with persistent volumes

**Organizations:**
- **Tenant isolation** — Deploy one instance per team/department with isolated data
- **Kubernetes** — Run 10, 100, or 1000+ isolated instances on the same cluster
- **Flexible resources** — Small instances (0.5 CPU / 1 GB RAM) for teams; dedicated nodes for enterprises

**Enterprise:**
- PostgreSQL backend for advanced features
- Dedicated infrastructure or self-hosted on-premises
- Custom authentication (SSO, LDAP) and compliance requirements

### Why Tenant Isolation?

Instead of building a complex multi-tenant system with `tenant_id` columns everywhere, Workspace uses **instance-per-tenant**:

- **True data isolation** — No risk of accidental data leaks between users
- **Independent scaling** — One team's traffic spike doesn't affect others
- **Simplified security** — Each instance can have its own encryption keys, backups, and compliance rules
- **Predictable costs** — Pay for what each team uses, not for complex shared infrastructure

This approach prioritizes operational simplicity, security, and the freedom to deploy however you want — from a single Raspberry Pi to a Kubernetes cluster serving thousands of teams.

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

Built with boring, reliable technology:

| Layer              | Technology                                     |
|--------------------|------------------------------------------------|
| **Backend**        | Django 6.0, Django REST Framework              |
| **Frontend**       | Alpine.js, Tailwind CSS, DaisyUI, Lucide Icons |
| **Database**       | SQLite (WAL mode) or PostgreSQL                |
| **Cache / Broker** | Redis (optional, for Celery tasks)             |
| **Server**         | Gunicorn, WhiteNoise, Brotli compression       |
| **Tooling**        | uv, Docker, drf-spectacular (OpenAPI)          |

No build steps, no frontend framework complexity, no microservices. Just Python and templates.

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (recommended package manager)
- Redis (optional, for caching and Celery)

## Getting Started

### Quick Start (Local Development)

```bash
# Clone the repository
git clone <repository-url>
cd Workspace

# Install dependencies (using uv)
uv sync

# Run migrations
python manage.py migrate

# Create a superuser
python manage.py createsuperuser

# Start the development server
python manage.py runserver
```

Visit `http://localhost:8000` — that's it! No webpack, no npm, no build step.

## Deployment

### Docker (Recommended)

```bash
docker build -t workspace .
docker run -d -p 8000:8000 \
  -v workspace-db:/app/db \
  -v workspace-files:/app/files \
  -e SECRET_KEY=your-secret-key-here \
  -e ALLOWED_HOSTS=yourdomain.com \
  workspace
```

### Docker Compose

```yaml
version: '3.8'
services:
  workspace:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./db:/app/db
      - ./files:/app/files
    environment:
      SECRET_KEY: your-secret-key-here
      ALLOWED_HOSTS: localhost,yourdomain.com
      DATABASE_ENGINE: sqlite  # or postgres
```

### Kubernetes (For Multi-Tenant Deployments)

Deploy isolated instances per team/client:

```yaml
# Example: One pod per tenant with dedicated resources
apiVersion: apps/v1
kind: Deployment
metadata:
  name: workspace-client-a
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: workspace
        image: workspace:latest
        resources:
          requests:
            cpu: 500m
            memory: 1Gi
          limits:
            cpu: 4000m
            memory: 8Gi
```

See [deployment docs](docs/deployment.md) for full k8s manifests and best practices.

### Environment Variables

| Variable           | Description                      | Default               |
|--------------------|----------------------------------|-----------------------|
| `SECRET_KEY`       | Django secret key                | *required*            |
| `DEBUG`            | Enable debug mode                | `False`               |
| `ALLOWED_HOSTS`    | Comma-separated allowed hosts    | `localhost,127.0.0.1` |
| `DATABASE_ENGINE`  | Database backend (sqlite/postgres) | `sqlite`            |
| `SQLITE_PATH`      | Path to SQLite database          | `db.sqlite3`          |
| `DATABASE_URL`     | PostgreSQL connection string     | *(none)*              |
| `REDIS_URL`        | Redis connection URL             | *(none)*              |
| `GUNICORN_WORKERS` | Number of Gunicorn workers       | `3`                   |
| `DJANGO_LOG_LEVEL` | Logging level                    | `INFO`                |

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

## Extending Workspace

### Module System

Workspace is designed to be modular. Each module (Files, Dashboard, Notes, Tasks, etc.) registers itself dynamically at startup.

**Adding a new module:**

1. Create a Django app under `workspace/`
2. Register a `ModuleInfo` in your app's `AppConfig.ready()` method:
   ```python
   from workspace.core.registry import registry, ModuleInfo

   class MyModuleConfig(AppConfig):
       def ready(self):
           registry.register(ModuleInfo(
               name='my_module',
               display_name='My Module',
               icon='sparkles',  # Lucide icon name
               color='primary',
               url='/my-module/',
           ))
   ```
3. Optionally register a search provider for unified search

Your module automatically appears in the sidebar and search results.

### API-First Design

Every feature is accessible via REST API. Build mobile apps, CLI tools, or integrations without touching the web UI.

- **Full OpenAPI schema** — `/api/v1/schema/`
- **Interactive docs** — `/api/v1/schema/swagger-ui/`
- **No authentication quirks** — Standard Django REST Framework auth

### Health Checks

Kubernetes-ready health endpoints at `/health/` for liveness and readiness probes.

## Roadmap

Workspace is in active development. Planned modules include:

- 📝 **Notes & Wiki** — Rich text editor with backlinks and page hierarchy
- ✅ **Tasks & Projects** — Kanban boards, sprints, time tracking
- 📧 **Email Client** — IMAP/SMTP integration with unified inbox
- 📅 **Calendar** — CalDAV sync, recurring events, availability slots
- 👥 **Contacts & CRM** — Contact management with interaction history
- 💬 **Chat** — Real-time messaging with channels and threads
- 🔗 **Bookmarks** — Save and organize links with automatic previews
- 🔐 **Password Manager** — Encrypted vault with TOTP support

See [IDEAS.md](IDEAS.md) for the complete roadmap and implementation details.

## Contributing

Contributions are welcome! Whether you're fixing bugs, adding features, or improving documentation:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

By contributing, you agree to the contributor license grant in `CONTRIBUTING.md`.

## License

Workspace is source-available under the Business Source License 1.1 (BSL 1.1).

- Free to use, modify, and self-host for personal or internal business use.
- You may not offer Workspace as a hosted or managed service to third parties without a commercial license.
- This version will become available under the MIT License on 2030-02-01.

See `LICENSE` for full terms and `CHANGELOG.md` for the version timeline.
