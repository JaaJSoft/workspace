# Changelog

## 0.18.0

### Highlights

Performance and reliability release. Listings, sidebars, and notifications across the workspace are noticeably faster. Large WebDAV uploads are more reliable on slow networks. Rename and permission rules are now consistent between the UI and the backend — no more buttons that look clickable but fail. New personal API tokens let you connect third-party apps and scripts to your workspace.

### Performance

- Faster conversation, folder, mail, and calendar listings across the app
- Quicker loading of pinned folders and favorites
- Faster delivery of chat notifications in busy conversations
- Snappier response on pages that read user settings

### API Tokens

- Generate personal API tokens to authenticate third-party apps and scripts against the workspace API, with dedicated login and logout endpoints

### WebDAV

- Large uploads now stream directly to storage, reducing memory pressure and improving reliability on slow networks
- Fixed a rare crash when a file was deleted during an active upload

### Chat

- Search filter in the conversation sidebar to quickly find conversations
- Smoother refreshes of the sidebar, read receipts, and list updates — interactions no longer reset state mid-action

### Files & Notes

- Rename and action buttons now match the backend rules — the UI only offers what will actually succeed
- Journal notes can no longer be renamed by mistake
- File name validation blocks invalid characters before save
- Properties panel, pinned folders, and group sidebar refresh without flicker

### Profile & UI

- Refresh button added to the profile activity feed
- Generic help dialog with collapsible sections for cleaner navigation

### Fixes

- Multi-step operations are now fully transactional, preventing rare partial updates
- User settings are no longer fetched for anonymous visitors

## 0.17.0

### Highlights

Calendar overhaul with infinite scroll and a smoother mobile experience. WebDAV reliability improves on Windows, and concurrent uploads no longer cause duplicates or corruption. Notes gains new keyboard shortcuts.

### Calendar

- Infinite scroll across events — no more pagination arrows
- Sidebar collapse is more reliable, with a smoother mobile experience
- Improved hover interactions on both touch and non-touch devices
- Events from external feeds are no longer mistakenly attributed to your account
- Right-click context menu no longer flashes before appearing

### Notes

- New keyboard shortcuts, with an updated help dialog to browse them

### WebDAV

- Fixed large file uploads from Windows clients
- Uploading the same file concurrently no longer creates duplicates or corrupts data
- Upload coordination now works correctly across multi-worker deployments

### Fixes

- Activity events with no actor no longer break the activity feed

## 0.16.0

### Highlights

**Profile customization** arrives with bio, role, and banner palette. Rich media in chat gets a major boost — link previews, a shared media gallery, and video attachments analyzable by the AI. WebDAV now shows your storage quota, and broad caching makes heavy pages noticeably snappier.

### Profile

- **Customize your profile** with a bio, role, and banner palette

### Chat & AI

- **Link previews** for URLs shared in messages
- **Shared media gallery** in the conversation info panel
- **Video attachments** with frame extraction for AI analysis
- Filter input in the AI chatbot picker dialog
- AI replies now have temporal awareness in conversation history

### Calendar

- Improved agenda view

### Notes

- "Move" and "Open in Files" actions in the note manager

### WebDAV

- **Storage quota tracking** showing used and available bytes

### Performance

- Faster page loads thanks to broader caching (views, files, chat responses)
- Quicker calendar recurrence handling
- Faster database queries on heavy pages

### Fixes

- Folder content table layout and text handling in list view
- WebDAV methods now route correctly on the root path
- Calendar details wrap text correctly for location and description
- Declined events no longer appear in the upcoming calendar view
- Activity feed no longer hides others' events when the actor is excluded

## 0.15.0

### Highlights

**Subscribe to external calendars** (ICS) with automatic background sync. **Group folders** bring shared file spaces across teams. Notes gains a **context menu**, autosave indicators, and default/journal folder preferences.

### Calendar

- **Subscribe to external calendars** (ICS) with automatic background sync
- Action buttons on events from external calendars
- Recurring events from ICS now honor the repeat-count limit correctly

### Chat & AI

- **Rolling conversation summaries** keep AI context within limits while preserving long-running discussions
- AI tool call history now persists across sessions
- Empty AI summaries no longer break conversation updates

### Files

- **Group folders** — shared folder spaces with creation dialog and sidebar integration
- Destructive actions on root group folders are blocked
- Group folders and sidebar refresh automatically after changes

### Notes

- Default folder and journal folder selectable in preferences
- **Context menu** on notes with rename, delete, move, and more
- Create subfolders directly from the context menu
- Icons for sidebar sections (Quick Access, Tags, Folders, Groups)
- Help dialog with keyboard shortcut reference
- **Autosave** with save-status indicators in the Markdown editor

### Fixes

- Mobile back navigation in Mail and Notes
- Unread counts in unified inbox update correctly
- Un-favoriting a note keeps the selection consistent
- Smoother chat membership updates and read receipts

## 0.14.0

### Highlights

**Notes**, a new Markdown-based note-taking app, joins the workspace — with tags, advanced filters, and folder-tree sidebar organization. Mail now lands on a **unified inbox** with customizable density. A new workspace-wide **Favorites** view lets you pin content from every module.

### New: Notes

Markdown-based note-taking app with rich organization features.

- Tag notes and track your activity
- Advanced filters and search with highlighted matches
- Context menu on folders and tags, including "hide from sidebar"
- Folder tree with expand/collapse in the sidebar
- Refresh button and action dialogs for note management

### Mail

- **Unified inbox** as the default landing page
- Customizable preferences: density, preview lines, and label visibility
- Improved mobile support and responsiveness

### Calendar

- New AI tool to check your availability
- Notifications only sent for future events
- Event comparisons now respect timezones correctly

### Dashboard

- Improved tab layout and responsiveness

### UI

- Dynamic quick actions and recent commands tracking
- **"Favorites" view** across all modules
- "Open in Files" option in context menu
- Selected folder/label reflected in the URL (for sharing and refresh)
- Mobile navigation with sidebar toggle
- Favorite toggle for images
- Responsive button sizing in note and message lists

### Fixes

- Poll icons update immediately after voting
- Fixed an SVG rendering infinite loop
- File size display handles invalid inputs gracefully
- Un-favoriting respects edit permissions
- Improved reconnection and error handling for live updates
- Markdown editor padding on smaller screens
- Changelog modal width on smaller screens

## 0.13.0

### Highlights

**Share files with anyone** via password-protected, expiring links. Mail gets smarter — automatic detection of deleted or moved messages, and cleaner AI classification for sent and draft folders.

### Files

- **Shareable file links** with password protection and expiration dates

### AI & Bots

- More robust parsing of AI tool calls
- Image generation now handles a broader range of image-related requests

### Mail

- **Folder reconciliation** automatically detects deleted and moved messages
- Pending actions now skip inactive accounts
- AI classification skipped for sent and drafts folders

### UI

- Dark theme typography reads correctly in modals
- Message loading no longer interrupts auto-scroll
- Fixed stale messages briefly appearing when switching conversations

### Fixes

- Better IMAP flag sync with precise state diffs
- Fixed edge cases in IMAP folder synchronization
- Scheduled messages no longer post empty responses

## 0.12.0

### Highlights

AI gains **web search**, **scheduled messages**, and dedicated search tools for calendar, chat, mail, and files. Mail introduces **AI-powered labels**, and the app becomes installable as a **PWA** with offline caching. A new "What's new" viewer lets you browse the changelog directly from the user menu.

### AI & Bots

Enhanced AI capabilities with web search, scheduling, and improved tool handling.

- **Web search** and webpage reading
- **Scheduled messages** with timezone-aware delivery
- Dedicated search tools for calendar, chat, mail, and files
- AI **image editing** with multi-provider fallback
- Auto-retry for empty AI responses
- Prompt refinements: factual accuracy, natural tool use, memory integration

### Chat

- **Typing indicators** in real time
- Bot conversations get auto-generated titles
- Reliable reconnection when returning to the app on mobile
- Better rendering of AI-generated images

### Mail

- **Label management with AI-driven classification**
- Unread counts per label
- Activity tracking split: sent mail for the profile heatmap, received mail for the dashboard
- Reconnecting a disconnected OAuth2 account no longer creates duplicates
- When an OAuth2 token is revoked, the account deactivates and you get a notification
- Improved AI summary rendering and folder/label UI

### Dashboard & UI

- **"What's new" modal** accessible from the user menu
- Redesigned inline alerts with a subtle border style
- **PWA support** with offline caching and app icons
- Workspace usage stats with count-up animations and storage quota
- Improved search bar responsiveness
- Session expiry gracefully handled

### Users

- Timezone-aware scheduling and user settings

### Fixes

- Scheduled messages convert to UTC correctly
- AI badge layout handles multiple tools
- Clearer AI image edit error messages
- Duplicate files from trashed folders during sync
- Chat titles generate only after 2+ messages
- Calendar widget accent color consistency

## 0.11.0

### Highlights

Major overhaul of **AI bots**: bots now remember context, mention users, search the workspace, and generate or edit images, with fine-grained access and capability controls. Chat gains **drafts**, **@mentions**, and syntax highlighting. The dashboard welcomes you with a **personalized greeting**, a weather widget, and upcoming events.

### AI & Bots

AI tools ecosystem and bot management overhaul.

- **AI Memory** — bots remember context across sessions, with search and filter UI
- **Image generation and editing** tools for bots
- **Workspace search tool** — bots can query across all modules
- Dedicated Mail, Files, and Chat tools
- Message search and user info retrieval tools
- **Bot access controls** — public visibility settings and capability flags
- Customize bot avatars and appearance
- Personalized system prompt with the bot's name in context
- Configurable timeout, retry options, and context size

### Chat

- **Drafts** saved and restored per conversation
- **@mentions** with notifications
- Syntax highlighting and richer Markdown rendering
- **Clear Conversation** feature
- Delete bot messages with proper UI handling
- Custom bot avatars in chat UI
- Redesigned input bar for mobile and desktop
- Faster unread count updates (every 5 seconds)

### Dashboard & UI

- **Personalized greeting** with a dynamic weather widget
- User profile with activity feed, stats, and a contribution heatmap
- **Upcoming events** dashboard widget
- Custom error pages (400, 403, 404, 500)
- "Superuser" label replaced with a cleaner "Admin" badge
- Navbar alignment and responsiveness improvements

### Fixes

- Calendar icons refresh correctly after polls update
- Greeting falls back to username when first name is empty

## 0.10.0

### Highlights

**AI Assistant** lands across Chat and Mail — bots respond in conversations (text and images), summarize emails, and help you compose replies. Mail adds **OAuth2 authentication** for providers like Gmail and Microsoft.

### New: AI Assistant

AI-powered assistant integrated across Chat and Mail modules.

- Configurable AI bots with a picker modal and per-conversation assignment
- **Chat AI** — bots respond in conversations with text and image attachments
- **Mail AI** — email summaries with a dismiss option, preserving formatting
- **Mail AI** — reply assistance using your sender identity for tone
- Editor task type with attachment viewer for AI-generated content
- Bots show presence status

### Mail

- **OAuth2 authentication** for mail accounts
- Hidden folders support
- Folder tags displayed in search results

### Chat

- **Push notifications** for new messages
- Mark-as-read clears chat notification badges

### Search

- Tags support in search results

### Calendar

- Document title reflects the currently open poll

### Admin

- Admin interfaces for AI, notifications, and user settings

### Fixes

- Presence indicators disabled in dialog avatars
- Visual refresh for the mail account menu

## 0.9.0

### Highlights

**Calendar polls** — schedule events democratically by proposing time slots, inviting guests (even without an account), collecting votes, and picking the final slot. Chat gets an **emoji picker** and optimistic message sending. **File locking** prevents concurrent editing conflicts.

### Calendar

- **Poll scheduling** — create polls with time slots, invite guests via shareable link, collect votes, pick the final slot
- Edit polls by adding or removing slots; redesigned poll list with search and filters
- Optional notifications when guests vote on your polls
- **iCalendar email integration** — incoming `.ics` attachments are processed and replies sent automatically
- Event-specific URLs in notifications for direct navigation
- Pending actions now include events until end of day
- Invitation calendar name updates when your account display name changes

### Chat

- **Emoji picker** for messages and reactions
- Messages appear immediately with a loading animation — no waiting for the server
- Smoother scroll handling and delayed image loading
- Read receipt dropdown position corrected

### Files

- **File locking** with lock/unlock UI and API to prevent concurrent editing conflicts
- Real-time file event notifications (edits, lock releases)

### Notifications

- **Web Push** support

### Dashboard

- App grid with pending action badges (unread counts per module)
- **Command palette** with registration and search

### Performance

- Faster real-time event delivery thanks to push-based notifications
- Quicker event and poll loading

### Fixes

- Mail unread counts stay in sync with optimistic UI updates

## 0.8.0

### Highlights

Chat gets **message replies** with quoted preview and click-to-scroll, plus **read receipts** with detail popovers. File and calendar activity now produce dedicated notifications.

### Chat

- **Reply to messages** with a quoted preview — click the quote to scroll to the original
- **Read receipts** with double-check indicators, per-group read count, and a detail popover
- Message timestamps moved to the group footer alongside read receipts

### Notifications

- **File activity notifications** — edits, shares, permission changes, deletions, and comments
- **Calendar event notifications** — invites, updates, cancellations, and RSVP responses
- Notification URLs and click handling now work reliably

## 0.7.0

### Highlights

A real-time **notification system** lands with its own UI panel. User **presence tracking** (online, away, busy, invisible) shows you who's around, with DM shortcuts from profiles and user cards.

### Notifications

- **Notification system** with a dedicated UI panel and real-time delivery

### Users

- **Presence tracking** — online, away, offline detection
- **Manual status** — online, away, busy, invisible
- User card popover with real-time status updates
- **DM shortcut** from user profiles and user cards
- Logging out immediately marks you as offline

### Chat

- Faster conversation list thanks to cached unread counts
- Older messages show the year for clarity

### UI

- Timestamps render in your local timezone across the app
- Folder content timestamps follow the same rule
- Fixed horizontal overflow in the message container
- App shortcuts no longer conflict with browser shortcuts
- Navbar cleanup: removed unused entries

## 0.6.0

### Highlights

**Mail**, a new IMAP/SMTP mail client, joins the workspace — with account auto-discovery, drafts, a hierarchical folder tree, drag-and-drop message management, and direct "Save to Files". Calendar gains **recurring events** with scope-aware editing. Kubernetes deployment manifests are now available for self-hosters.

### New module: Mail

IMAP/SMTP mail client integrated into the workspace.

- Account setup with **auto-discovery** of IMAP/SMTP settings
- Compose dialog with reply/forward detection, drafts, and attachments
- **Hierarchical folder tree** with subfolders, move, and drag-and-drop
- Customize folder icons and colors
- Filter messages by search, unread, starred, or attachments
- Drag-and-drop or context menu to move messages
- Contact autocomplete with popover cards
- **"Save to Files"** — save mail attachments directly to the file browser
- Sent mail properly stored on the server (IMAP APPEND)
- Syncing indicators, loading spinners, and empty states throughout
- Context menu on messages with action shortcuts
- Selected message reflected in the URL for sharing
- Help dialog with shortcuts and features
- Edit mail account settings from a dialog

### Calendar

- **Recurring events** with scope-aware edit and delete (this one, this and future, all)

### Chat

- **Pin messages** in conversations
- Conversation descriptions
- Search filters for messages

### Dashboard

- Conversation and event insights widgets

### Files

- **Upload progress tracking** with redesigned toast notifications
- Folder picker component for file selection
- Loading states for file actions and empty trash

### UI

- Loading skeletons for dashboard content
- Search results now show dates
- Fixed text overflow in dialog messages

### Infrastructure

- **Kubernetes deployment manifests** with health probes (liveness, readiness, startup)
- **Celery task queue** for background processing, with Redis fallback

## 0.5.0

### Highlights

Calendar introduces an **Agenda view** with a chronological event listing. Chat now supports **attachments** — upload files to messages and save them directly to your file browser. Files gets a **comments** system.

### Calendar

- **Agenda view** — chronological list of events across your calendars
- Event context menu with quick actions (edit, delete, duplicate)
- Show or hide declined events
- Smoother loading of the event detail panel
- Fixed all-day event formatting during event creation

### Chat

- **Message attachments** — upload and attach files to messages
- **"Save to Files"** — save chat attachments directly to your file browser

### Files

- **Comments on files** — add, edit, and delete
- Refreshed properties panel

### Users

- User mini profile popover when hovering avatars

### Infrastructure

- **Docker images** now published on GHCR for each `main` push and tag

## 0.4.0

### Highlights

Two major new modules land: **Chat** (real-time messaging with direct and group conversations, reactions, Markdown, search) and **Calendar** (month/week/day views, multiple calendars, guest invitations). Files gains **sharing with granular permissions**, thumbnails, and a mosaic view.

### New module: Chat

Real-time messaging system with direct and group conversations.

- **Direct messages and group chats** with real-time delivery
- Grouped message display with **emoji reactions**
- Message editing, deletion, and Markdown formatting (bold, italic, code, strikethrough)
- **Conversation search** with keyboard navigation across message history
- Group avatars with image cropping
- Member management: add, remove, context menu actions
- Conversation info panel with stats (Alt+I)
- **Pinned conversations** with drag-and-drop reordering
- Collapsible sidebar with unread badges
- Keyboard shortcuts: Enter to send, ↑ to edit last message, Ctrl+B/I/E for formatting, Alt+N for new conversation, Ctrl+F for search
- Help dialog with full shortcut reference

### New module: Calendar

Full-featured calendar with multiple views and event management.

- **Month, week, and day views**
- **Multiple calendars** with color coding and visibility toggles
- Event creation with date/time pickers, location, and description
- All-day and timed events with quick duration shortcuts (30m, 1h, 2h...)
- **Guest invitations** with accept/decline workflow
- Right-side detail panel for event viewing
- Calendar preferences (default view, first day of week, time format, week numbers)
- View, date, and selected event reflected in the URL (for sharing and refresh)
- Keyboard shortcuts: ← → for navigation, M/W/D for views, T for today, N for new event
- Help dialog with shortcut reference

### Files

- **File sharing** with granular permissions and a share management UI
- Thumbnail generation for images and SVG files
- **Mosaic/grid view** with an adjustable tile size
- File viewer modal navigation (previous/next)
- Extensible action system for files
- Pinned folder context menu enhancements

### Users

- **Avatar upload** with image cropping
- User settings page with profile enhancements

### UI

- New prompt dialogs with icons and customizable input sizes
- New user selector with avatars, search-as-you-type, and keyboard navigation
- Shared dialog utilities: confirm, prompt, message, error — with icons

### Infrastructure

- **Trash auto-purge** — trashed items are now periodically cleaned up

## 0.3.0

### Highlights

Folders can now be **downloaded as ZIP archives**. A new "Download as ZIP" option appears in folder context menus, and the download endpoint transparently handles both files and folders.

### Files

- **Download folders as ZIP archives** — new "Download as ZIP" context menu option
- The download endpoint now handles both files and folders

## 0.2.0

### Highlights

**PostgreSQL support** — the workspace can now run against PostgreSQL as well as SQLite. Monaco editor's base theme is now in sync with the workspace theme.

### Infrastructure

- **PostgreSQL support** for production deployments

### UI

- Monaco editor base theme syncs with the workspace theme

## 0.1.0

### Highlights

Initial public release of the workspace. A **file browser** with built-in editors and viewers, **WebDAV integration**, a unified dashboard, and per-user settings.

### File Browser

- Navigation with breadcrumbs and keyboard shortcuts
- Drag & drop upload, favorites, trash, and bulk actions

### Editors & Viewers

- **Monaco Editor** for text and code files with a full toolbar and persisted preferences
- **Milkdown Crepe** WYSIWYG for Markdown with slash commands
- Image, PDF, and media viewers

### Workspace

- Dashboard, responsive sidebar, unified search, and help modal
- Modular architecture with dynamic module management

### Infrastructure

- **WebDAV integration** with authentication
- Per-user settings with theme selection
