# Changelog

## 0.10.0

### New: AI Assistant

AI-powered assistant integrated across Chat and Mail modules.

- Configurable AI bots with bot picker modal and per-conversation assignment
- Chat AI: bots respond in conversations with multimodal support (text and image attachments)
- Mail AI: email summarization with dismiss option and formatting preservation
- Mail AI: email composition and reply assistance with sender identity context
- Editor task type with attachment viewer for AI-generated content
- Bot presence status support

### Mail

- OAuth2 support for mail account authentication
- Hidden folders support
- Folder tags displayed in search results

### Chat

- Push notifications for new messages
- Mark-as-read clears chat notification badges

### Search

- Tags support in search results

### Calendar

- Dynamic document title updates with poll title

### Admin

- Admin interfaces for AI, notifications, and user settings

### Infrastructure

- Support for loading environment variables from `.env` files

### Fixes

- Disable presence indicators in user avatars for dialogs
- Mail account menu visual update

## 0.9.0

### Calendar

- Poll scheduling: create polls with time slots, invite guests via token, vote, and pick a final slot
- Poll editing with slot add/remove, redesigned poll list with search and filtered views
- Poll vote notifications for creators with preference toggle
- iCalendar email integration: process incoming `.ics` attachments and send REPLY via Celery task
- Event-specific URLs in notifications for direct navigation
- Optimize event and poll query performance with prefetching, indexing, and poll ID annotation
- Pending actions filter includes events until end of day
- Fix invitation calendar name when account display name changes

### Chat

- Emoji picker for message input and reactions
- Optimistic message sending with temporary bubbles and loading animation
- Improved scroll handling for messages and delayed image loading
- Fix read receipt dropdown positioning

### Files

- File locking with lock/unlock UI, API, and conflict prevention
- Real-time file event notifications via SSE (edits, lock releases)

### Notifications

- Web Push support with VAPID key configuration

### Dashboard

- App grid with pending action badges showing unread counts per module
- Command palette with registration and search support


### Infrastructure

- SSE: replace cache polling with Redis Pub/Sub for near-instant event delivery
- Replace `json` with `orjson` for faster serialization
- Dependency updates: redis 7.2.1, whitenoise 6.12.0, nh3 0.3.3, dj-database-url 3.1.2

### Fixes

- Mail: optimistic UI correctly handles unread count updates

## 0.8.0

### Chat

- Message reply feature with quoted preview, click-to-scroll, and SSE support
- Read receipts with double-check indicator, per-group read count, and detail popover
- Message timestamps moved to group footer alongside read receipt indicators

### Notifications

- File action notifications: edits, shares, permission changes, deletions, and comments
- Calendar event notifications: invites, updates, cancellations, and RSVP responses
- Fix URL generation and click handling in notification items

### UI

- Centralized action loading state with Alpine.js store replacing per-component `actionLoading`
- `show_presence` parameter on user avatar component for optional presence indicator
- Optimized presence queries with Django Q objects

## 0.7.0

### Notifications

- Notification system with UI panel, SSE real-time delivery, and API

### Users

- Presence tracking system with online, away, and offline detection
- Manual status selection: online, away, busy, invisible
- Internal `last_activity` field to track real activity regardless of status
- User card popover with real-time status updates
- DM shortcut from user profile and user card
- Clear presence on logout so user appears offline immediately

### Chat

- Denormalized unread counts for faster conversation list rendering
- Global SSE provider architecture replacing per-component connections
- Year format for older messages in time display

### UI

- `localtime_tag` template filter for client-side timezone formatting (time, date, datetime, relative, full)
- Folder content timestamps use `localtime_tag`
- Fix horizontal overflow in message container
- Fix keyboard shortcuts overriding browser/system shortcuts
- Navbar cleanup: remove unused "Health Check" and "Add item" entries

### Infrastructure

- Prometheus metrics integration
- PostgreSQL connection pooling support with `psycopg`
- Enhanced admin site with additional models, filters, and search

## 0.6.0

### New module: Mail

IMAP/SMTP mail client integrated into the workspace.

- Account management with auto-discovery of IMAP/SMTP settings
- Compose dialog with reply/forward detection, draft management, and attachments
- Hierarchical folder tree with subfolder creation, move, and drag-and-drop
- Folder icon and color customization via context menu
- Message filters: search, unread, starred, and attachments
- Drag-and-drop and menu-based message move between folders
- Contact autocomplete with search and contact card popovers
- "Save to Files" action to save mail attachments directly to the file browser
- Sent mail handling with IMAP APPEND support
- Syncing indicators, loading spinners, and empty states throughout
- Message context menu with action shortcuts
- URL state management: selected message reflected in URL
- Help dialog with shortcuts, features, and API access
- Edit account dialog for updating mail account settings

### Calendar

- Recurring event support with scope-aware edit and delete (single, this and future, all)

### Chat

- Message pinning functionality
- Conversation descriptions
- Filters for message search with expanded UI and backend support

### Dashboard

- Conversation and event insights widgets

### Files

- File upload progress tracking with redesigned toast notifications
- Folder Picker component for file selection
- Loading states for file actions and empty trash

### UI

- Loading skeletons for dashboard content
- Search results enhanced with date display
- Fix text overflow in dialog messages

### Infrastructure

- Kubernetes deployment manifests with health probes and volume configurations
- Custom health check views for Kubernetes liveness, readiness, and startup probes (replaces `django-health-check`)
- Celery task queue configuration with Redis fallback
- Gevent async worker support for Gunicorn
- Dependency updates: redis 7.1.1, gunicorn 25.0.3

## 0.5.0

### Calendar

- Agenda view with chronological event listing
- Event context menu with quick actions (edit, delete, duplicate)
- Show/hide declined events
- Skeleton loading state for event detail panel
- Fix all-day event formatting in form initialization

### Chat

- Message attachments: upload and attach files to messages
- "Save to Files" action to save chat attachments directly to the file browser

### Files

- File comments system with add, edit, and delete support
- Enhanced properties panel UI

### Users

- User mini profile popover on avatar hover

### Infrastructure

- CI workflow to build and push Docker images to GHCR on `main` and tags
- Database indexes added across calendar, chat, and files apps
- Comprehensive API test suite for calendars and events
- Dockerfile build and runtime refinements
- Dependency update: Pillow

## 0.4.0

### New module: Chat

Real-time messaging system with direct and group conversations.

- Direct messages and group chats with real-time delivery via Server-Sent Events
- Grouped message display with reactions (emoji picker)
- Message editing, deletion, and Markdown formatting (bold, italic, code, strikethrough)
- Conversation search with keyboard navigation across message history
- Group avatar support with image cropping
- Member management: add/remove members, context menu actions
- Conversation info panel with stats (Alt+I)
- Pinned conversations with drag-and-drop reordering
- Collapsible sidebar with unread badges
- Keyboard shortcuts: Enter to send, arrow up to edit last message, Ctrl+B/I/E for formatting, Alt+N for new conversation, Ctrl+F for search
- Help dialog with full shortcut reference and feature documentation

### New module: Calendar

Full-featured calendar with multiple views and event management.

- Month, week, and day views powered by FullCalendar
- Multiple calendars with color coding and visibility toggles
- Event CRUD with date/time pickers, location, and description
- All-day and timed events with quick duration shortcuts (30m, 1h, 2h...)
- Guest invitations with accept/decline workflow
- Right-side detail panel for event viewing
- Calendar preferences (default view, first day of week, time format, week numbers) persisted via API
- URL state management: view, date, and selected event reflected in URL for sharing and refresh
- Keyboard shortcuts: arrow keys for navigation, M/W/D for views, T for today, N for new event
- Help dialog with shortcut reference and feature documentation

### Files

- File sharing system with granular permissions and share management UI
- Thumbnail generation for images and SVG files with rasterization
- Mosaic/grid view with adjustable tile size slider
- File viewer modal navigation controls (prev/next)
- Extensible file action system with action registry
- OpenAPI schema documentation for file management API
- Pinned folder context menu enhancements

### Users

- Avatar upload with image cropping and profile picture management
- User settings page with profile enhancements
- Secure avatar eTag generation (HMAC-SHA256)

### Common

- Reusable prompt dialog component (AppDialog.prompt with icon, input size options)
- Reusable user selector component with avatars, search-as-you-type, and keyboard navigation
- Shared dialog utilities: confirm, prompt, message, error with icon support

### Infrastructure

- Trash auto-purge periodic task
- SQLite database maintenance command and periodic task
- Read-only CI workflow permissions
- Dependency updates: Django 6.0.2, gunicorn 25.0.1, django-health-check 3.23.3
- WebDAV caching hash improvements (PBKDF2-HMAC)
- Health check page UI cleanup

## 0.3.0

- Add folder download as ZIP archive via `/api/v1/files/<uuid>/download`
- Extend file download endpoint to support both files and folders
- Update context menu with download option for folders ("Download as ZIP")
- Fix release script consistency and license change date formatting

## 0.2.0

- Add PostgreSQL support via `dj-database-url` and `psycopg`
- Sync Monaco editor basic theme with DaisyUI theme
- Add GitHub Actions CI workflow for running tests
- Configure Dependabot for `uv` package ecosystem
- Add release automation script

## 0.1.0

Initial public release.

- File browser with navigation, breadcrumbs, keyboard shortcuts, drag & drop upload, favorites, trash, and bulk actions
- Monaco Editor for text/code files with full toolbar and persisted preferences
- Milkdown Crepe WYSIWYG for Markdown with slash commands and DaisyUI theme integration
- Image, PDF, and media viewers
- WebDAV integration with Django authentication
- Per-user settings system with theme selection
- Dashboard, responsive sidebar, unified search, and help modal
- Module registry with dynamic module management
