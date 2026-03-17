# Workspace

> **Early Development** — This project is under active development and has not reached a stable release. APIs, database schemas, and features may change without notice or migration path. Use at your own risk.

A self-hosted, modular productivity suite built with Django. Workspace brings together file management, messaging, email, calendar, AI assistants, and more into a unified platform you control.

**Run anywhere, scale everywhere** — from a Raspberry Pi to a Kubernetes cluster.

## Why Workspace?

Most productivity suites force you to choose: simple self-hosted tools that don't scale, or complex SaaS platforms you don't control.

Workspace gives you both: **run anywhere, scale everywhere**.

### Simplicity First
At its core, Workspace is just Django + SQLite. You can run it on a Raspberry Pi, a $5/month VPS, or your laptop. No complex setup, no database servers to manage, no infrastructure lock-in.

- **SQLite (WAL mode)** — Production-ready, zero-maintenance database. Perfect for individuals and small teams.
- **Single docker deployment** — One Docker container, one process, done.
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
- Drag & drop file upload with visual overlay and progress tracking
- Built-in viewers for PDF, Markdown, images, video, audio, and code (with syntax highlighting)
- Favorites, pinned folders, and persistent sort preferences
- Soft-delete with trash and configurable retention (30 days)
- File copy with conflict resolution
- Folder download as ZIP archive
- Custom folder icons and colors
- File sharing with read-only / read-write permissions per user
- File locking to prevent concurrent editing
- Automatic thumbnail generation for images and SVGs
- Mosaic/grid view with adjustable tile size
- File comments with edit and soft-delete
- Extensible file action registry (open, transfer, organize, edit, etc.)
- File viewer navigation with Previous/Next and keyboard arrows
- Folder picker component for file selection
- WebDAV access — mount your files as a network drive on any OS

### Chat & Messaging
- Direct messages (1:1) and group conversations
- Real-time updates via Server-Sent Events (SSE)
- Emoji reactions with grouped display
- File attachments with upload, download, and "Save to Files" integration
- Message search with filters and highlight navigation
- Message pinning
- Message editing and soft-delete
- Read receipts with reader list
- Conversation descriptions and custom avatars
- Pinned conversations with drag-and-drop reordering
- Member management with context menus
- Rich Markdown rendering with syntax highlighting (Pygments), tables, and task lists
- AI bot integration — add bots to conversations for intelligent assistance
- Keyboard shortcuts and help dialog

### Calendar & Scheduling
- Day, week, month, and agenda views (FullCalendar)
- Events with title, description, location, and participants
- Recurring events with scope-aware edit and delete (single, this and future, all)
- Invitations and RSVP (pending / accepted / declined)
- Calendar subscriptions (subscribe to other users' calendars)
- Multiple calendars per user with color coding
- Scheduling polls — Doodle-style polls to find the best meeting time, with guest participation (no account required)
- iCalendar (RFC 5545) and iTIP support for email-based invitations
- Event context menu with quick actions (edit, delete, duplicate)
- Show/hide declined events
- Keyboard shortcuts and help dialog

### Mail
- IMAP/SMTP integration with auto-discovery of server settings
- OAuth2 authentication for Google (Gmail/Google Workspace) and Microsoft (Outlook/Office 365)
- Compose dialog with reply/forward detection, draft management, and attachments
- Hierarchical folder tree with subfolder creation, move, and drag-and-drop
- Folder icon and color customization
- Message filters: search, unread, starred, and attachments
- Drag-and-drop message move between folders
- Batch operations: mark read/unread, star, delete, move
- Contact autocomplete with search and contact card popovers
- "Save to Files" action to save mail attachments to the file browser
- AI-powered email summarization, composition, and reply suggestions
- Sent mail handling with IMAP APPEND support
- Help dialog with shortcuts, features, and API access

### AI Assistants
- AI-powered chat bots with configurable models and system prompts
- Email intelligence: summarization, composition assistance, and reply generation
- Text editor actions for AI-assisted writing
- Image generation — bots can generate images from text prompts (DALL-E, Stable Diffusion, or any compatible API)
- Bot memory system — bots remember context about users across conversations
- Support for vision (image understanding), function calling, and extended thinking
- Access control: public bots, private bots, or group-restricted
- Token usage tracking
- Compatible with OpenAI API and any OpenAI-compatible provider (Ollama, LM Studio, etc.)

### Notifications
- In-app notification center with priority levels (low, normal, high, urgent)
- Web Push notifications via VAPID protocol — get notified even when the browser tab is closed
- Read tracking and bulk mark-as-read

### Dashboard
- Storage statistics and file/folder counts
- Recent files, favorites, and trash overview
- Conversation and event insights widgets
- Module registry with quick access

### Unified Search
- Cross-module search via command palette (Ctrl+K)
- Results grouped by type with date display (files, folders, conversations, events, messages, etc.)
- Extensible search provider system

### User Settings & Profiles
- Per-user, per-module key-value settings store
- Avatar upload with Cropper.js and WebP conversion
- User mini profile popover on hover
- Enhanced profile page with stats and activity timeline
- Presence and status management (online, away, busy, invisible)
- Password management with configurable validation rules
- 12 themes (light, dark, cupcake, emerald, corporate, forest, dracula, night, winter, nord, sunset, autumn)

## Tech Stack

Built with boring, reliable technology:

| Layer              | Technology                                                   |
|--------------------|--------------------------------------------------------------|
| **Backend**        | Django 6.0, Django REST Framework                            |
| **Frontend**       | Alpine.js, Tailwind CSS, DaisyUI, Lucide Icons               |
| **Real-time**      | Server-Sent Events (SSE)                                     |
| **Database**       | SQLite (WAL mode) or PostgreSQL                              |
| **Cache / Broker** | Redis (optional, for caching, sessions, and Celery tasks)    |
| **Task Queue**     | Celery with Redis broker, Celery Beat for periodic tasks     |
| **Server**         | Gunicorn with gevent workers, WhiteNoise, Brotli compression |
| **AI**             | OpenAI API (or any compatible provider)                      |
| **Imaging**        | Pillow, CairoSVG (thumbnail generation)                      |
| **Monitoring**     | Prometheus metrics, Kubernetes health probes                 |
| **Tooling**        | uv, Docker, drf-spectacular (OpenAPI)                        |

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

**Basic (no AI):**

```bash
docker build -t workspace .
docker run -d -p 8000:8000 \
  -v workspace-db:/app/db \
  -v workspace-files:/app/files \
  -e SECRET_KEY=your-secret-key-here \
  -e ALLOWED_HOSTS=yourdomain.com \
  workspace
```

**With AI assistants and image generation:**

```bash
docker run -d -p 8000:8000 \
  -v workspace-db:/app/db \
  -v workspace-files:/app/files \
  -e SECRET_KEY=your-secret-key-here \
  -e ALLOWED_HOSTS=yourdomain.com \
  -e AI_API_KEY=sk-your-openai-key \
  -e AI_MODEL=gpt-4o \
  -e AI_IMAGE_MODEL=dall-e-3 \
  workspace
```

**With a self-hosted LLM (Ollama) and separate image service:**

```bash
docker run -d -p 8000:8000 \
  -v workspace-db:/app/db \
  -v workspace-files:/app/files \
  -e SECRET_KEY=your-secret-key-here \
  -e ALLOWED_HOSTS=yourdomain.com \
  -e AI_API_KEY=ollama \
  -e AI_BASE_URL=http://ollama:11434/v1 \
  -e AI_MODEL=llama3 \
  -e AI_IMAGE_BASE_URL=https://api.openai.com/v1 \
  -e AI_IMAGE_MODEL=dall-e-3 \
  workspace
```

### Environment Variables

| Variable               | Description                                                                         | Default                      |
|------------------------|-------------------------------------------------------------------------------------|------------------------------|
| `SECRET_KEY`           | Django secret key                                                                   | *required in production*     |
| `DEBUG`                | Enable debug mode (`1`, `true`, `yes`, `on`)                                        | `True`                       |
| `ALLOWED_HOSTS`        | Comma-separated allowed hosts                                                       | `*`                          |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated trusted CSRF origins                                                | *(none)*                     |
| `DATABASE_URL`         | Database connection string (`sqlite:///db.sqlite3`, `postgres://user:pass@host/db`) | `sqlite:///db.sqlite3`       |
| `REDIS_URL`            | Redis URL for cache and sessions (alias: `DJANGO_REDIS_URL`)                        | *(none, in-memory fallback)* |
| `STATIC_ROOT`          | Collected static files directory                                                    | `staticfiles`                |
| `DJANGO_LOG_LEVEL`     | Logging level                                                                       | `INFO`                       |
| `TRASH_RETENTION_DAYS` | Days before trashed items are permanently deleted                                   | `30`                         |
| `GUNICORN_WORKERS`     | Gunicorn worker count (Docker)                                                      | `3`                          |
| `AI_API_KEY`           | OpenAI API key (or compatible provider)                                             | *(none, AI disabled)*        |
| `AI_BASE_URL`          | Custom base URL for the LLM API (Ollama, LM Studio, etc.)                           | *(OpenAI default)*           |
| `AI_MODEL`             | Default LLM model for chat and tasks                                                | `gpt-5`                      |
| `AI_MAX_TOKENS`        | Maximum tokens per AI response                                                      | `2048`                       |
| `AI_CHAT_CONTEXT_SIZE` | Number of recent messages included as context                                       | `150`                        |
| `AI_IMAGE_MODEL`       | Model for image generation                                                          | `dall-e-3`                   |
| `AI_IMAGE_BASE_URL`    | Custom base URL for image generation (falls back to `AI_BASE_URL`)                  | *(same as AI_BASE_URL)*      |

## CI/CD

- **Tests** — GitHub Actions runs the test suite on every push and pull request (Python 3.14 + uv)
- **Docker** — Automatic multi-arch image builds pushed to GitHub Container Registry on `main` and version tags
- **Dependabot** — Automated dependency updates for the uv ecosystem

## API

All endpoints are prefixed with `/api/v1/` and use no trailing slashes.

### Documentation

Interactive API documentation is available when the server is running:

- **Swagger UI** — `/api/v1/schema/swagger-ui/`
- **ReDoc** — `/api/v1/schema/redoc/`
- **OpenAPI Schema** — `/api/v1/schema/`

## Extending Workspace

### Module System

Workspace is designed to be modular. Each module (Files, Chat, Calendar, Mail, AI, etc.) registers itself dynamically at startup.

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

Kubernetes-ready health endpoints for liveness, readiness, and startup probes:

- `/health/live` — Liveness probe
- `/health/ready` — Readiness probe (checks database connectivity)
- `/health/startup` — Startup probe

## Roadmap

Workspace is in active development. Shipped and planned modules:

**Shipped:**
- **Files** — Hierarchical file browser, sharing, WebDAV, thumbnails, comments
- **Chat** — Direct messages, group conversations, reactions, file attachments, message pinning, search, rich Markdown with syntax highlighting
- **Calendar** — Day/week/month/agenda views, recurring events, participants, invitations & RSVP, subscriptions, scheduling polls
- **Mail** — IMAP/SMTP client with OAuth2, auto-discovery, compose, folders, filters, contact autocomplete
- **AI** — Chat bots, email intelligence, editor actions, image generation, bot memory, multi-provider support
- **Notifications** — In-app notifications, Web Push, priority levels

**Planned:**
- **Notes & Wiki** — Rich text editor with backlinks and page hierarchy
- **Tasks & Projects** — Kanban boards, sprints, time tracking
- **Contacts & CRM** — Contact management with interaction history
- **Bookmarks** — Save and organize links with automatic previews
- **Password Manager** — Encrypted vault with TOTP support

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
- This version will become available under the MIT License on 2029-03-17.

See `LICENSE` for full terms and `CHANGELOG.md` for the version timeline.
