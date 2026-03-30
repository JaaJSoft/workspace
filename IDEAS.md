# Workspace - Ideas & Roadmap

Your workspace, all in one place.


## Quick Wins

Quick improvements with high impact on existing features.

- [x] **Drag & drop upload** - Drag files from OS directly into the browser
- [x] **Preview PDF** - Integrated PDF viewer (pdf.js)
- [x] **Preview Markdown** - Markdown rendering in the file viewer
- [x] **Preview code** - Syntax highlighting (highlight.js / Shiki)
- [x] **Global keyboard shortcuts** - Ctrl+K command palette, Ctrl+N new, Delete, Ctrl+C/V
- [x] **Folder sizes** - Recursive size calculation displayed in properties
- [ ] **Clickable breadcrumb in properties** - Navigate to parent from modal
- [x] **Multi-select with Shift+Click** - Range selection in the file browser
- [x] **Download file/folder** - Download a file or folder (zip)
- [ ] **File/Folder ln** - Symbolic links for files and folders to add share folders or files to your workspace
- [ ] **File/Folder permissions** - Read/Write/Delete permissions for files and folders
- [x] **File/Folder tags** - Add custom labels to files and folders (Tag + FileTag models with icons and extended UI)
- [x] **Group folders** - Shared folders linked to auth groups with member-scoped access
- [ ] **User quota** - Storage limit per user with gauge in dashboard
- [x] **Persistent Dark/Light mode** - Save chosen theme server-side
- [x] **Enhanced toast notifications** - Notification stack with auto-dismiss
- [x] **Empty states** - Illustrations when a folder/view is empty
- [x] **Persistent sorting** - Remember user's chosen sort order (cookie/DB)
- [x] **Avatar upload** - User profile picture with Cropper.js and WebP conversion
- [x] **Thumbnails** - Automatic thumbnail generation for images and SVGs (WebP, Pillow, cairosvg)
- [x] **Mosaic/grid view** - Card-based grid layout with adjustable tile size slider
- [x] **File sharing** - Share files with RO/RW permissions per user
- [x] **Shareable file links** - Public links with password protection and expiration
- [x] **File comments** - Comments on files with edit and soft-delete
- [x] **File actions registry** - Extensible action system with categories (open, transfer, organize, edit, etc.)
- [x] **File viewer navigation** - Previous/Next controls with keyboard arrows
- [x] **User mini profile popover** - Hover-triggered user card with caching
- [x] **User settings page** - Profile, preferences, and security tabs
- [ ] **File/Folder versioning** - Track changes to files and folders
- [ ] **File/Folder history** - View previous versions of files and folders
- [ ] **Duplicate detection** - SHA-256 hash on upload, warn on duplicate files
- [ ] **Recent files widget** - Dashboard widget showing last 10 opened/modified files
- [ ] **Image annotation** - Draw/comment on images directly (canvas overlay)
- [ ] **Office document preview** - Preview DOCX/XLSX/PPTX (LibreOffice headless or Collabora)

---

## Modules Ecosystem

### 1. Notes & Wiki

An integrated collaborative document editor, Notion/Outline style. **Module shipped in v0.13.0.**

- [x] Rich text editor (Milkdown / ProseMirror)
- [x] Hierarchical pages (folder tree via Files module)
- [x] Native Markdown support
- [ ] Page templates (meeting notes, specs, daily standup)
- [ ] Page links (backlinks / graph)
- [ ] Embed files from the Files module
- [ ] Auto-generated table of contents
- [ ] Export to PDF / Markdown
- [ ] Version history (visual diff)
- [x] Favorites and pinned pages in the sidebar
- [x] Tags and labels with icons and filtering
- [x] Advanced search and filters with highlighting
- [x] Keyboard shortcuts and help dialog
- [x] Journal folder support with configurable defaults
- [x] Group folders (shared notes linked to auth groups)
- [x] Context menus for notes, folders, and tags
- [x] Autosave with granular save status

---

### 2. Tasks & Projects (Jira-like)

Project management and task tracking.

- [ ] Projects with Kanban board (drag & drop)
- [ ] List, board, calendar, timeline (Gantt) views
- [ ] Tasks with title, description (rich text), assignee, priority, labels
- [ ] Subtasks and checklists
- [ ] Customizable statuses per project
- [ ] Sprints with start/end dates
- [ ] Saved filters and custom views
- [ ] Task comments
- [ ] Attachments (link with Files module)
- [ ] Auto task numbering (PROJ-123)
- [ ] Time estimation and time tracking
- [ ] Project dashboard (burndown chart, velocity)
- [ ] Notifications on assignment and mentions
- [ ] Recurring tasks

---

### 3. Email Client

Integrated email client to centralize communication.

- [x] IMAP/SMTP connection (multi-account, with OAuth2 support)
- [x] Unified inbox (default landing page with cross-account aggregation)
- [x] Compose, reply, forward
- [x] Attachments -> direct save to Files
- [x] iCalendar integration — parse incoming .ics invitations (REQUEST/CANCEL), auto-create calendar events, send METHOD:REPLY on accept/decline
- [x] Custom labels / tags (with AI-driven email classification)
- [x] Customizable mail preferences — density, preview lines, label visibility
- [x] Mobile-responsive layout with sidebar toggle
- [ ] Full-text search in emails
- [ ] HTML signatures per account
- [ ] Snooze / reminders
- [ ] Convert email to task (link with Tasks)
- [ ] Email templates
- [ ] Filters and automatic rules
- [ ] Scheduled send — compose now, send later via Celery delayed task
- [ ] Undo send — 10s grace period before actually sending (queue + cancel)

---

### 4. Calendar & Scheduling

Calendar and planning. **Module shipped in v0.4.0.**

- [x] Day, week, month views (FullCalendar integration)
- [x] Events with title, description, location, participants
- [x] Recurring events
- [x] External ICS calendar sync — subscribe to external ICS feeds with auto-sync (ETag, interval)
- [ ] Bidirectional CalDAV / Google Calendar / Outlook sync
- [ ] Reminders (email, in-app notification)
- [x] Quick event creation — click a time slot or drag-to-select, type title, expand for details
- [ ] Drag to resize events — resize duration by dragging bottom edge
- [ ] Multi-calendar overlay — see multiple users' calendars side by side
- [x] Scheduling polls (Doodle-style) — create polls with time slots, invite participants, vote, finalize with share link
- [ ] Link with tasks (deadlines visible in calendar)
- [x] Agenda view (chronological list)
- [ ] Time zones (format preferences only for now)
- [x] Invitations and RSVP (pending/accepted/declined)
- [x] Calendar subscriptions (subscribe to other users' calendars)
- [x] Keyboard shortcuts (M/W/D/T/N/Esc/?) and help dialog

---

### 5. Contacts & CRM

Contact management and customer relationship.

- [ ] Contact cards (name, email, phone, company, notes)
- [ ] Companies / organizations
- [ ] Tags and segments
- [ ] Interaction history (sent emails, meetings, related tasks)
- [ ] Import/export CSV, vCard
- [ ] Advanced search and filters
- [ ] Deal pipeline (simplified CRM)
- [ ] Merge duplicates
- [ ] Custom fields

---

### 6. Chat & Messaging

Real-time internal communication. **Module shipped in v0.4.0.**

- [ ] Public and private channels
- [x] Direct messages (1:1 and groups)
- [ ] Discussion threads
- [x] File sharing (upload, download, save to Files module)
- [x] Emoji reactions (toggle, grouped display, real-time SSE)
- [x] @user and @everyone mentions with badge rendering and hover cards
- [x] Message search (full-text within conversations, highlight navigation)
- [x] Real-time updates (SSE-based streaming with unread counts)
- [x] Online / away / busy status
- [x] Pinned conversations with drag-and-drop reordering
- [x] Pin individual messages within a conversation
- [ ] Integration with Tasks (create task from message)
- [x] Message attachments with "Save to Files" integration
- [x] Group conversations with avatar, stats, and info panel
- [x] Member management and context menus
- [x] Keyboard shortcuts and help dialog
- [x] Markdown message rendering — rich formatting with Pygments syntax highlighting, tables, task lists
- [x] Typing indicators — "X is typing..." via SSE (cache key + short TTL)
- [x] Message replies — quote a specific message when replying (parent_id FK)
- [ ] Link previews — auto-unfurl URLs with OpenGraph metadata (title, image, description)
- [x] Read receipts — show who has read messages in group conversations
- [ ] Voice messages — record and send audio clips (MediaRecorder API + file upload)
- [x] Message formatting toolbar — bold/italic/strikethrough/code/link buttons + keyboard shortcuts
- [x] AI chatbot integration — configurable bot profiles with tool calling, rolling conversation summaries, persistent memory, scheduled messages

---

### 7. Bookmarks & Links

Bookmark manager and monitoring.

- [ ] Save URLs with title, description, tags
- [ ] Automatic title and favicon capture
- [ ] Page screenshot/preview
- [ ] Collections / bookmark folders
- [ ] Import from browser (HTML bookmark file)
- [ ] Full-text search
- [ ] Share collections
- [ ] Dead link detection
- [ ] Browser extension for one-click save
- [ ] Offline reading (page archive)

---

### 8. Time Tracking

Work time tracking.

- [ ] Start/stop timer with associated project and task
- [ ] Manual time entry
- [ ] Weekly timesheet view
- [ ] Reports by project, client, period
- [ ] Export CSV / PDF
- [ ] Weekly goals
- [ ] Native integration with Tasks (track from a task)
- [ ] Dashboard with distribution charts
- [ ] Integrated Pomodoro timer
- [ ] Billable vs non-billable

---

### 9. Passwords & Secrets

Password and secrets manager.

- [ ] Encrypted vault (AES-256)
- [ ] Entries: login, password, URL, notes, TOTP
- [ ] Password generator
- [ ] Categories and tags
- [ ] Quick search
- [ ] Secure clipboard copy (auto-clear)
- [ ] Access audit log
- [ ] Import from Bitwarden, 1Password, KeePass (CSV)
- [ ] Client-side encryption (zero-knowledge)
- [ ] Secure secret sharing (temporary link)

---

### 10. Dashboards & Analytics

Customizable dashboards.

- [ ] Configurable widgets (stats, charts, lists)
- [ ] Dashboard per module (files, tasks, time, etc.)
- [ ] Drag & drop to organize widgets
- [ ] Custom KPIs
- [ ] Charts (Chart.js / Apache ECharts)
- [x] Global activity dashboard (cross-module feed with activity provider registry)
- [x] Upcoming events widget (today's calendar events)
- [x] Module stats and pending action counts
- [ ] Report export
- [ ] Shared dashboards between users
- [ ] Starred/favorites unification — single "favorites" system across files, conversations, events

---

### 11. Snippets & Code

Code snippet manager.

- [ ] Snippets with syntax highlighting
- [ ] Multi-language support
- [ ] Tags and categories
- [ ] Full-text search in code
- [ ] Snippet versioning
- [ ] One-click copy
- [ ] Embed in Notes/Wiki
- [ ] Import from GitHub Gists
- [ ] Shared collections
- [ ] Diff support (compare two versions)

---

### 12. Forms & Surveys

Form and survey creation.

- [ ] Drag & drop form builder
- [ ] Field types: text, choice, date, file, note, etc.
- [ ] Conditional logic (show if...)
- [ ] Shareable link (public or authenticated)
- [ ] Response collection and export (CSV, JSON)
- [ ] Notifications on new response
- [ ] Form templates
- [ ] Integration with Tasks (create task per response)
- [ ] Response statistics

---

## Transversal / Infrastructure

Features shared across all modules.

### Global Search
- [x] Unified search across all modules (Ctrl+K) with search provider registry
- [ ] Full-text indexing — PostgreSQL FTS (tsvector/tsquery) across files, mail, chat, contacts
- [x] Results grouped by type (file, task, note, contact...)
- [x] Recent commands tracking and quick actions

### Notifications
- [x] In-app notification center
- [x] SSE for real-time notifications (same pattern as chat)
- [ ] Notification preferences per module
- [ ] Email digest (daily/weekly)
- [x] Push notifications (VAPID keys, service worker, Celery task)

### Users & Teams
- [x] Enhanced user profiles (avatar, stats, activity timeline, settings page)
- [ ] Teams / groups (group conversations exist in Chat, no org-level teams yet)
- [ ] Roles and permissions per module (file-level RO/RW sharing exists)
- [ ] Email invitation
- [ ] SSO (SAML, OAuth2 - Google, GitHub, Microsoft)
- [ ] 2FA (TOTP)
- [ ] Global audit log

### API & AI
- [x] AI chatbot platform — bot profiles, tool registry, rolling summaries, persistent memory
- [x] AI tools — search messages, check availability, file operations, mail operations, image generation
- [x] Scheduled AI messages — recurring/one-time bot messages via Celery
- [x] AI email classification — automatic label assignment on incoming mail
- [ ] Configurable webhooks
- [ ] Personal API tokens
- [ ] Zapier / n8n / Make integration
- [ ] Global import/export (JSON)
- [ ] CLI for automated interactions
- [ ] Python/JS SDK

### Desktop Integration
- [x] WebDAV server (wsgidav) — mount files as a network drive on any OS (WIP)
- [ ] macOS File Provider Extension — native Finder integration with sync status
- [ ] Windows Cloud Files API — native Explorer integration (on-demand files)

### Ops & Maintenance
- [x] **SQLite maintenance job** — Celery Beat task (daily 3h) : PRAGMA optimize, WAL checkpoint, VACUUM, integrity check. Commande `manage.py db_maintenance` pour exécution manuelle.
- [x] **Trash auto-purge** — Tâche Celery (daily 2h30) pour hard-delete les fichiers en corbeille depuis > `TRASH_RETENTION_DAYS`. Commande `manage.py purge_trash` avec `--days` et `--dry-run`.
- [x] **Orphan attachments purge** — Celery task + management command `purge_orphan_attachments` for chat file cleanup
- [ ] **Session cleanup** — Tâche Celery pour `clearsessions` (purge des sessions DB expirées quand Redis n'est pas utilisé)
- [x] **Admin enrichi** — File, FileFavorite, PinnedFolder, ConversationMember, PinnedMessage, PinnedConversation dans l'admin avec filtres et recherche
- [ ] **Background file processing** — Celery pipeline for thumbnails, virus scan, metadata extraction on upload
- [ ] **CDN / S3 storage backend** — django-storages for scalable file storage (MinIO for self-hosted)
- [ ] **Rate limiting** — django-ratelimit on API endpoints (login, file upload, chat send)
- [x] **Metrics endpoint** — Prometheus /metrics (request latency, DB pool, cache hit rate, active SSE connections)
- [ ] **Structured logging** — JSON logs with request_id tracing (django-structlog)
- [x] **Database connection pooling** — PgBouncer or psycopg pool for high-concurrency scenarios
- [x] **View caching** — CacheControlMixin with invalidation for API responses
- [ ] **E2E tests** — Playwright test suite for critical flows (login, file upload, chat send, mail compose)

### UI/UX
- [x] PWA (Progressive Web App) — service worker, push notifications, offline caching, app icons
- [x] Mobile responsive design — Tailwind responsive classes across all modules
- [x] Modular sidebar (each module = one section)
- [x] Customizable themes (12 themes: light, dark, cupcake, emerald, corporate, forest, dracula, night, winter, nord, sunset, autumn)
- [ ] Onboarding wizard for new users
- [ ] Focus mode (hide sidebar) — per-module sidebar collapse exists (Chat, Calendar)
- [x] Keyboard shortcuts per module (Files, Calendar, Chat, global Ctrl+K)
- [ ] i18n (FR, EN minimum) — Django i18n enabled but no translations yet
- [x] Reusable dialog system (confirm, prompt, message)
- [x] Enhanced user profiles with avatar, stats, and activity timeline (basic for now)
- [x] Changelog modal — in-app release notes viewer
- [x] Reusable preferences UI partials (density, toggles, popovers)
