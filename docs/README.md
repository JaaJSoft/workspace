# Documentation

Documentation for [Workspace](../README.md) - a self-hosted productivity suite built with Django.

## Modules

| Module | Description |
|---|---|
| [Files](files/) | Upload, organize, preview, and share files; WebDAV, thumbnails, trash |
| [Chat](chat/) | Direct and group messaging, real-time updates, AI bots, audio calls |
| [Calendar](calendar/) | Multiple views, recurring events, RSVP, scheduling polls, iCalendar |
| [Mail](mail/) | IMAP/SMTP client with OAuth2, AI summaries, folders and labels |
| [Notes](notes/) | Markdown notes with journal mode, folders, tags, full-text search |
| [AI Assistants](ai/) | Configurable bots with tools, vision, memory, and scheduled messages |
| [Notifications](notifications/) | In-app and Web Push notifications with priority and read tracking |

## Deployment

- [Deployment overview](deployments/) - modes, reverse proxy, required headers
- [Docker Compose](deployments/docker-compose/) - single-node setup
- [Kubernetes](deployments/kubernetes/) - cluster setup

## Guides

- [Migrating from SQLite to PostgreSQL](guides/sqlite-to-postgres.md)

## API reference

Interactive API documentation is served by the running instance:

- Swagger UI - `/schema/swagger-ui/`
- ReDoc - `/schema/redoc/`
- OpenAPI schema - `/schema/`
