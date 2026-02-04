# Workspace - Ideas & Roadmap

## Quick Wins

Quick improvements with high impact on existing features.

- [x] **Drag & drop upload** - Drag files from OS directly into the browser
- [x] **Preview PDF** - Integrated PDF viewer (pdf.js)
- [x] **Preview Markdown** - Markdown rendering in the file viewer
- [x] **Preview code** - Syntax highlighting (highlight.js / Shiki)
- [x] **Global keyboard shortcuts** - Ctrl+K command palette, Ctrl+N new, Delete, Ctrl+C/V
- [ ] **Folder sizes** - Recursive size calculation displayed in properties
- [ ] **Clickable breadcrumb in properties** - Navigate to parent from modal
- [ ] **Multi-select with Shift+Click** - Range selection in the file browser
- [x] **Download file/folder** - Download a file or folder (zip)
- [ ] **File/Folder ln** - Symbolic links for files and folders to add share folders or files to your workspace
- [ ] **File/Folder permissions** - Read/Write/Delete permissions for files and folders
- [ ] **File/Folder tags** - Add custom labels to files and folders
- [ ] **team folders** - Share files with other users
- [ ] **User quota** - Storage limit per user with gauge in dashboard
- [x] **Persistent Dark/Light mode** - Save chosen theme server-side
- [x] **Enhanced toast notifications** - Notification stack with auto-dismiss
- [x] **Empty states** - Illustrations when a folder/view is empty
- [x] **Persistent sorting** - Remember user's chosen sort order (cookie/DB)
- [ ] **Avatar upload** - User profile picture
- [ ] **File/Folder versioning** - Track changes to files and folders
- [ ] **File/Folder history** - View previous versions of files and folders

---

## Modules Ecosystem

### 1. Notes & Wiki

An integrated collaborative document editor, Notion/Outline style.

- [ ] Rich text editor (Tiptap / ProseMirror)
- [ ] Hierarchical pages (tree structure like files)
- [ ] Native Markdown support
- [ ] Page templates (meeting notes, specs, daily standup)
- [ ] Page links (backlinks / graph)
- [ ] Embed files from the Files module
- [ ] Auto-generated table of contents
- [ ] Export to PDF / Markdown
- [ ] Version history (visual diff)
- [ ] Pin pages in the sidebar

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

- [ ] IMAP/SMTP connection (multi-account)
- [ ] Unified inbox
- [ ] Compose, reply, forward
- [ ] Attachments -> direct save to Files
- [ ] Custom labels / tags
- [ ] Full-text search in emails
- [ ] HTML signatures per account
- [ ] Snooze / reminders
- [ ] Convert email to task (link with Tasks)
- [ ] Email templates
- [ ] Filters and automatic rules

---

### 4. Calendar & Scheduling

Calendar and planning.

- [ ] Day, week, month views
- [ ] Events with title, description, location, participants
- [ ] Recurring events (RRULE)
- [ ] Sync CalDAV / Google Calendar / Outlook
- [ ] Reminders (email, in-app notification)
- [ ] Availability slots (Calendly style)
- [ ] Link with tasks (deadlines visible in calendar)
- [ ] Agenda view (chronological list)
- [ ] Time zones
- [ ] Invitations and RSVP

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

Real-time internal communication.

- [ ] Public and private channels
- [ ] Direct messages (1:1 and groups)
- [ ] Discussion threads
- [ ] File sharing (link with Files)
- [ ] Emoji reactions
- [ ] @user and @channel mentions
- [ ] Message search
- [ ] Push notifications (WebSocket)
- [ ] Online / away / busy status
- [ ] Pin important messages
- [ ] Integration with Tasks (create task from message)

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
- [ ] Global activity dashboard (feed)
- [ ] Report export
- [ ] Shared dashboards between users

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
- [x] Unified search across all modules (Ctrl+K)
- [ ] Full-text indexing (PostgreSQL FTS or Meilisearch)
- [x] Results grouped by type (file, task, note, contact...)
- [ ] Recent searches and suggestions

### Notifications
- [ ] In-app notification center
- [ ] WebSocket for real-time
- [ ] Notification preferences per module
- [ ] Email digest (daily/weekly)
- [ ] Push notifications (PWA)

### Users & Teams
- [ ] Enhanced user profiles
- [ ] Teams / groups
- [ ] Roles and permissions per module
- [ ] Email invitation
- [ ] SSO (SAML, OAuth2 - Google, GitHub, Microsoft)
- [ ] 2FA (TOTP)
- [ ] Global audit log

### API & Integrations
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
- [ ] **Session cleanup** — Tâche Celery pour `clearsessions` (purge des sessions DB expirées quand Redis n'est pas utilisé)
- [ ] **Admin enrichi** — Enregistrer File, FileFavorite, PinnedFolder dans l'admin Django avec filtres et recherche

### UI/UX
- [ ] PWA (Progressive Web App) - installable on desktop/mobile
- [ ] Mobile responsive design
- [x] Modular sidebar (each module = one section)
- [x] Customizable themes
- [ ] Onboarding wizard for new users
- [ ] Focus mode (hide sidebar)
- [ ] Keyboard shortcuts per module
- [ ] i18n (FR, EN minimum)
