# Workspace - Ideas & Roadmap

Your workspace, all in one place.

This file is the forward-looking roadmap and idea backlog, sorted by priority. Shipped work is recorded in [CHANGELOG.md](CHANGELOG.md); items here get checked off and pruned as they ship.

**Positioning:** a self-hosted productivity suite for solo users and small teams. Every module must work great for one person and get richer with more users. From a Raspberry Pi to a Kubernetes cluster.

**Working rhythm:** one structural project at a time (a new module or a cross-cutting foundation), with quick wins on existing modules alongside. The AI thread runs through everything: each new module ships its AI tools with it (as calendar did).

---

## Priorities at a glance

| Priority       | Structural projects                                                                | Quick wins                                                                                             |
|----------------|------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| **P1 - Now**   | Full-text search everywhere; Tasks module MVP                                      | Perf audit fixes, calendar reminders, recent files widget, clickable breadcrumb, drag-to-resize events |
| **P2 - Next**  | Tasks integrations (mail/chat/calendar/AI); Contacts module; i18n (FR)             | Mail snooze, scheduled send, undo send, email digest, per-module notification preferences              |
| **P3 - Later** | File versioning + quotas; Trust (2FA, SSO, teams, audit log); "ask your workspace" | Duplicate detection, chat voice messages, note templates, notes PDF export                             |
| **Someday**    | Bookmarks, Photos & Gallery, RSS/Feeds, cross-module automations, CalDAV sync      | Module backlogs below                                                                                  |

---

## Foundations (cross-cutting projects)

### Full-text search - P1

The biggest transversal gap: no indexed search anywhere. Mail search is a sequential scan of the largest table (perf audit H4), and every module rolls its own `icontains`. Doing this first means Tasks is born with proper search instead of adding another sequential scan.

- [ ] PostgreSQL FTS (tsvector/tsquery) on mail messages first - fixes audit H4 and its five duplicated call sites
- [ ] Extend to notes, chat messages, files (name + extracted text), contacts later
- [ ] Trigram index (pg_trgm) for autocomplete paths (mail contacts fires per keystroke)
- [ ] Plug into the existing Ctrl+K search provider registry - results grouped by type already works
- [ ] SQLite fallback (FTS5) or graceful degradation to icontains for dev

### Ask your workspace - P3

Once FTS exists, expose it as an AI tool so the assistant can answer "find the email where we discussed the quote" across mail, notes, chat, and files. All the bot plumbing (tool registry, memory, summaries) already exists.

- [ ] Cross-module search tool for bots
- [ ] Answer with linked sources (deep links into the module UIs)

### Trust & teams - P3

Required as soon as "small teams" is real usage.

- [ ] 2FA (TOTP)
- [ ] SSO (django-allauth: Google, GitHub, Microsoft; SAML later if asked)
- [ ] Global audit log
- [ ] Org-level teams / groups (group conversations and group folders exist, no team entity yet)
- [ ] Email invitation flow
- [ ] Roles and permissions per module

### Cross-module automations - Someday

Generalize mail rules into a trigger/action registry: "when a mail from X arrives, create a task", "when a file lands in folder Y, notify the group". Reuses the ActionRegistry pattern. Strong differentiator vs Nextcloud.

- [ ] Trigger registry (mail received, file created, event upcoming, task overdue...)
- [ ] Action registry (create task, send notification, label, move, call webhook)
- [ ] Rule builder UI (condition + action, per user)
- [ ] Outgoing webhooks, then incoming webhooks / Zapier / n8n

---

## Quick wins backlog

High impact, low effort, on existing modules.

**P1:**

- [ ] **Perf audit fixes (H1-H4)** - actions endpoint N+1, synchronous calendar badge, RRULE expansion from series start, mail search scan (see `docs/query-performance-audit-2026-07-06.md`)
- [ ] **Calendar reminders** (in-app + push) - the notification infra (VAPID, service worker, Celery) is already in place, best value/effort ratio of the list
- [ ] **Recent files widget** - dashboard widget showing last 10 opened/modified files
- [ ] **Clickable breadcrumb in properties** - navigate to parent from modal
- [ ] **Drag to resize events** - resize duration by dragging bottom edge

**P2:**

- [ ] **Mail scheduled send** - compose now, send later via Celery delayed task
- [ ] **Mail undo send** - 10s grace period before actually sending (queue + cancel)
- [ ] **Mail snooze / reminders**
- [ ] **Per-module notification preferences**
- [ ] **Email digest** (daily/weekly)
- [ ] **i18n (FR, EN)** - Django i18n is enabled, zero translations exist

**P3:**

- [ ] **Chat voice messages** - MediaRecorder API + existing attachment upload
- [ ] **Note page templates** (meeting notes, specs, daily standup)
- [ ] **Notes export to PDF / Markdown**
- [ ] **Duplicate detection** - SHA-256 hash on upload, warn on duplicate files

---

## New modules

In priority order.

### 1. Tasks & Projects - P1 (MVP), P2 (integrations)

The missing pillar for a product called "Workspace", and the module that multiplies the value of the others.

**MVP - P1:**

- [ ] Projects with Kanban board (drag & drop) and list view
- [ ] Tasks with title, rich description, assignee, priority, labels, due date
- [ ] Subtasks and checklists
- [ ] Customizable statuses per project
- [ ] Task comments
- [ ] Auto task numbering (PROJ-123)
- [ ] Notifications on assignment and mentions

**Integrations - P2:**

- [ ] Convert email to task
- [ ] Create task from chat message
- [ ] Due dates visible in calendar
- [ ] Attachments (link with Files module)
- [ ] Dashboard widget (my tasks, overdue)
- [ ] AI tools (create/list/complete tasks from the assistant)

**Later:**

- [ ] Sprints, timeline (Gantt), burndown/velocity charts
- [ ] Saved filters and custom views
- [ ] Time estimation
- [ ] Recurring tasks

### 2. Contacts - P2

A central address book feeding the rest: mail autocomplete is currently per-account, calendar participants and chat could share one source.

**MVP:**

- [ ] Contact cards (name, email, phone, company, notes, avatar)
- [ ] Companies / organizations
- [ ] Tags and advanced search
- [ ] Import/export CSV, vCard
- [ ] Auto-harvest from mail (suggest contacts from correspondence)
- [ ] Feed mail/calendar/chat autocomplete
- [ ] Merge duplicates

**Later (only on real demand):**

- [ ] Interaction history (sent emails, meetings, related tasks)
- [ ] Deal pipeline (simplified CRM), custom fields

### 3. Bookmarks & Read-it-later - Someday

Contained scope, very "self-hosted", reuses the chat `link_preview` service (OpenGraph) and the files tag system.

- [ ] Save URLs with title, description, tags
- [ ] Automatic title, favicon, and preview capture (reuse link_preview)
- [ ] Collections / bookmark folders
- [ ] Full-text search (rides the FTS foundation)
- [ ] Import from browser (HTML bookmark file)
- [ ] Share collections
- [ ] Later: browser extension, offline page archive, dead link detection

### 4. Photos & Gallery - Someday

A gallery/timeline/albums view on top of the files module. WebP thumbnails already exist; missing pieces are EXIF extraction and the UI. For the self-hosted audience this is an adoption magnet (in practice the number one Nextcloud use case).

- [ ] Timeline view (by capture date, EXIF)
- [ ] Albums (manual + per-folder)
- [ ] EXIF extraction in the existing Celery upload pipeline
- [ ] Lightbox viewer reusing file viewer navigation
- [ ] Later: shared albums, map view (GPS EXIF), "memories"

### 5. RSS / Feeds - Someday

Feed reader with Celery sync. Natural synergy with bookmarks (save article) and the email digest.

- [ ] Subscribe to RSS/Atom feeds, folders
- [ ] Background sync (Celery, ETag/Last-Modified like the ICS sync)
- [ ] Read/unread states, starring
- [ ] Save article to Bookmarks / Notes
- [ ] Later: OPML import/export, full-content extraction

---

## Deferred / requalified

- **Passwords & Secrets - frozen.** A zero-knowledge vault done right (client-side crypto) is a huge security project, and Vaultwarden exists. Clean up the leftover `workspace/passwords/` stub. Revive only out of genuine desire, not roadmap pressure.
- **Time Tracking - after Tasks.** Depends on tasks to track against; revisit once Tasks has real usage.
- **Snippets & Code - fold into Notes.** Code blocks with highlighting, tags, and search already exist there; a dedicated module is not worth the surface. Candidate note-level additions: one-click copy, Gist import.
- **Forms & Surveys - last.** Furthest from the core use case; revisit if a concrete need shows up.

---

## Module backlogs

Remaining ideas per shipped module, not yet prioritized (P1/P2/P3 items above are repeated here with their tag so each module's view stays complete).

### Files

- [ ] File/folder versioning and history (view previous versions) - P3
- [ ] User quota - storage limit per user with gauge in dashboard - P3
- [ ] Duplicate detection (SHA-256 on upload) - P3
- [ ] Recent files widget - P1
- [ ] Clickable breadcrumb in properties - P1
- [ ] File/Folder symbolic links - add shared folders or files to your workspace
- [ ] Finer file/folder permissions (read/write/delete)
- [ ] Image annotation - draw/comment on images (canvas overlay)
- [ ] Office document preview - DOCX/XLSX/PPTX (Gotenberg or Collabora; heavy operational dependency, decide deliberately)

### Notes

- [ ] Page templates - P3
- [ ] Export to PDF / Markdown - P3
- [ ] Embed files from the Files module
- [ ] Auto-generated table of contents
- [ ] Version history (visual diff)

### Mail

- [ ] Full-text search - P1 (FTS foundation)
- [ ] Snooze / reminders - P2
- [ ] Scheduled send - P2
- [ ] Undo send - P2
- [ ] Convert email to task - P2 (Tasks integration)
- [ ] Email templates

### Calendar

- [ ] Reminders (email, in-app notification) - P1
- [ ] Drag to resize events - P1
- [ ] Link with tasks (deadlines visible in calendar) - P2
- [ ] Bidirectional CalDAV / Google Calendar / Outlook sync - big project, Someday
- [ ] Multi-calendar overlay - see multiple users' calendars side by side
- [ ] Time zones (format preferences only for now)

### Chat

- [ ] Create task from message - P2 (Tasks integration)
- [ ] Voice messages - P3
- [ ] Public and private channels
- [ ] Discussion threads

### Dashboard

- [ ] Configurable widgets with drag & drop
- [ ] Custom KPIs and charts
- [ ] Shared dashboards between users
- [ ] Starred/favorites unification - single "favorites" system across files, conversations, events
- [ ] Report export

---

## Infrastructure & ops backlog

- [ ] **Session cleanup** - Celery task for `clearsessions` (expired DB sessions when Redis is not used)
- [ ] **CDN / S3 storage backend** - django-storages for scalable file storage (MinIO for self-hosted)
- [ ] **Rate limiting** - django-ratelimit on sensitive endpoints (login, file upload, chat send)
- [ ] **Structured logging** - JSON logs with request_id tracing (django-structlog)
- [ ] **E2E tests** - Playwright suite for critical flows (login, file upload, chat send, mail compose)
- [ ] **Desktop integration** - macOS File Provider Extension, Windows Cloud Files API (native Finder/Explorer sync on top of WebDAV)
- [ ] **API surface** - configurable webhooks, global import/export (JSON), CLI, Python/JS SDK (API tokens already shipped)

---

## Housekeeping

- [ ] Remove the empty `workspace/passwords/` module stub
- [ ] Keep this file honest: two stale items were found this round (chat link previews and personal API tokens were shipped but unchecked) - re-audit against the codebase when preparing each release
