# Documentation

Documentation for [Workspace](../README.md) - a self-hosted productivity suite built with Django.

## Modules

| Module | Description |
|---|---|
| [Files](files/) | Folders, uploads, viewers and editors, sharing, group folders, WebDAV |
| [Chat](chat/) | Direct and group conversations, threads, voice messages, audio and video calls |
| [Calendar](calendar/) | Recurring events, invitations, scheduling polls, CalDAV sync, ICS feeds |
| [Mail](mail/) | IMAP/SMTP client with OAuth2, labels, filters and rules, AI features |
| [Photos](photos/) | Timeline, albums, map, people, search by description |
| [Notes](notes/) | Markdown notes with journal, links, embeds and a graph view |
| [Projects](projects/) | Kanban or sprint boards, backlog, epics, timeline, reports |
| [People](people/) | Address books, lists, CardDAV sync, vCard import/export |
| [AI Assistants](ai/) | Bots with tools, memory, scheduled messages and long-running goals |
| [Vault](vault/) | End-to-end encrypted password manager (preview) |
| [Dashboard](dashboard/) | Home page: today's events, urgent tasks, app tiles, recent activity |
| [Notifications](notifications/) | Notification center, per-module levels and Web Push |
| [Imports](imports/) | Bring files and contacts over from Nextcloud or any WebDAV host |

## Deployment

- [Deployment overview](deployments/) - modes, reverse proxy, required headers
- [Docker Compose](deployments/docker-compose/) - single-node setup
- [Kubernetes](deployments/kubernetes/) - cluster setup

## Guides

- [Migrating from SQLite to PostgreSQL](guides/sqlite-to-postgres.md)
- [Monitoring with Prometheus](guides/monitoring.md) - `/metrics` credentials, scrape config, exposed series
- [Translations](guides/translations.md) - choosing the language, per-module catalogs, `manage.py translations`, marking strings

## API reference

Interactive API documentation is served by the running instance:

- Swagger UI - `/schema/swagger-ui/`
- ReDoc - `/schema/redoc/`
- OpenAPI schema - `/schema/`
