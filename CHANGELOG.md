# Changelog

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
