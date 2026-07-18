# Changelog

## 0.31.0 - Search Everywhere

### Highlights

Search just got a lot smarter across the app: mail, chat, and calendar now ignore accents, match whole words, and put the most relevant results first - and chat messages finally show up in the global search.

### Search

- Mail search now understands what you mean: matching is accent-insensitive ("cafe" finds "café"), works on whole words, and the most relevant messages come first instead of a raw date-ordered dump. It also stays fast on very large mailboxes. The same smarter matching applies in the mail app's search box, the global search, and when you ask the assistant to find a message.
- You can now find chat messages from the global search: results show the conversation they belong to, and direct messages are labeled with the other person's name. Searching inside a conversation and asking the assistant to search your messages use the same improved matching, with results ranked by relevance and recency.
- Calendar search now looks beyond event titles: a word from an event's description or its location is enough to find it, in the global search and when asking the assistant.

### Profile & UI

- People without a profile picture now get initials on a color of their own instead of a uniform grey circle. Each person keeps the same color everywhere - chat, mail, calendar, member lists - so they become recognizable at a glance.

### Highlights

Tag your files straight from the properties panel, and feel the app get faster across the board: mail search, calendars with recurring events, chat, and the file browser all respond quicker. The note editor also loads reliably now, with no dependence on an external service.

### Files

- You can now see and edit a file's tags in the properties sidebar. Tags show as colored badges, and the same tag picker as in notes lets you add, remove, create, and recolor tags in place, for the files you own.
- The Recent view opens noticeably faster, even with a large file library.
- Context menus and action buttons appear faster, especially in group folders and the "Shared with me" view.
- The graph view (when showing everything you can see) and the activity feed load faster.

### Notes & Editor

- The Markdown editor now ships with the app instead of being fetched from an external service at load time. It opens reliably every time, even when that service is down or you are offline. This fixes the "Failed to load editor" errors some users hit in notes and the files Markdown viewer.

### Mail

- Searching your mail and the recipient autocomplete when composing are much faster, especially on large mailboxes.
- Mail syncing is lighter and quicker, most visibly on the first sync of an account and when many rules are active.

### Calendar

- Calendars with long-running recurring events (say, a daily meeting created years ago) display much faster. Month, week, and day views, the upcoming widget, and reminders no longer slow down as a series gets older.
- Event search and activity feeds are quicker, and the event popover opens snappier.

### Chat

- Sending or receiving a message now updates just that conversation in the sidebar instead of redrawing the whole list: less flicker, snappier feel.
- The media panel (photos, videos, and files shared in a conversation) opens faster in media-heavy conversations.

### Profile

- Your profile page and its activity heatmap load faster.

### Fixes

- The presence ring around avatars no longer disappears for users whose avatar image fails to load and falls back to initials.
- Removed doubled-up padding in the breadcrumb dropdown menu of the file browser.

## 0.29.0 - Video Calls & Smarter Assistant

### Highlights

Calls now carry video and screen sharing, and they recover on their own when the network hiccups. Your assistant can read your calendar, book events, and check the weather anywhere. Plus a tidier dashboard you control and settings that finally live where they belong.

### Chat

- Calls now do video. Turn your camera on or off at any point during a call.
- Share your screen with everyone on the call. When someone starts sharing, their screen automatically takes the spotlight.
- Click any participant to blow them up into a large view with everyone else in a thumbnail strip; click again to return to the equal grid.
- A new connection diagnostic button in the call bar runs an in-call check so you can pinpoint trouble without leaving the call.
- Calls now heal themselves. If your connection briefly drops (switching Wi-Fi, moving networks), the call reconnects within a few seconds instead of going silent until it times out.
- Starting a call at the exact same moment as someone else in the same conversation no longer errors out; you both land in the same call.

### Assistant

- Your assistant can now work with your calendar: list your calendars, tell you what is coming up, and create a new event from a plain request like "add lunch with Sam on Friday at noon".
- Ask your assistant about the weather anywhere. "What's the weather in Tokyo?" or "is it raining in Paris?" now returns temperature, feels-like, humidity, wind, and sky conditions for any city, region, or country.

### Dashboard & Settings

- You can now choose which apps show on your dashboard. Hidden apps disappear from the dashboard grid only; they stay reachable from the navigation bar, search, and their direct links.
- Settings moved closer to where you use them. The global Settings page now holds only app-wide options (Profile, Appearance, Security, API Tokens, Usage), while each app's own preferences (dashboard layout, chat call sounds, mailbox AI features) now open from a popover on that app's page. The former "Preferences" tab is now "Appearance".

### Fixes

- Presence rings around avatars no longer randomly disappear, including on bot avatars and in dynamically updated lists like chat members and mentions.
- Your own presence ring now updates instantly when you change your status from the navbar, instead of lagging behind.
- Evened out the spacing on the notes sidebar folder links.

## 0.28.0 - Voice Calls & Notes Graph

### Highlights

Talk instead of type: start an audio call right inside a conversation. Plus tag filtering and hover cards in the notes graph, per-account mail signatures, and a friendlier welcome tour.

### Chat

- Audio calls are here. Anyone in a direct message or small group can start a call from the call button; other members see a "Call in progress" banner and join when they want, and the call ends when the last person hangs up. Calls run in their own room that stays connected while you browse the rest of the app: the room shows a participants grid that re-tiles as people join, a "who is speaking" indicator, your own self-view, and a live call timer, and you can keep chatting in the same window. In-call you can mute, see who else is on (with a muted indicator), and leave. Short sound cues play on join, leave, and mute (on by default, with an opt-out in chat preferences), and the history records when a call started and how long it lasted.
- A "Test call connection" button in chat preferences runs a quick self-test (microphone, network, and a full loopback through the server) so you can confirm calls work before getting on one.
- A conversation now jumps to the top of the list as soon as you send a message in it.
- The user-search dropdown no longer gets clipped inside conversation dialogs.

### Notes

- The graph view can now be filtered by tag: a new Tags button keeps only the notes carrying the tags you pick, with a search box to narrow a long tag list. Edges to hidden notes drop away accordingly.
- Hovering a note - on a graph node or an internal `[[link]]` in the editor - now shows a mini-card with its title, tags, and first line, plus an Open button.
- The graph shows a loading spinner while it builds, and panning or zooming no longer snaps back to a centered view.

### Mail

- Each mail account can now have its own signature. Set it from the account menu; it is added automatically when you compose, reply, or forward, and swaps when you change the sender account.
- Picking a folder - whether moving a message or setting up a rule - now uses one consistent list that matches your sidebar's order, icons, and colors, instead of three different pickers.

### Onboarding

- The welcome tour is now interactive: it greets you by name, lets you click straight into a module, skip the tour, and move between steps with the arrow keys, with better screen-reader and reduced-motion support.

### Fixes

- When image generation or editing comes back empty, the assistant now reports the failure instead of claiming success.
- Guest chips in the event and poll dialogs no longer overflow: the avatar and remove button line up cleanly inside the pill.
- Squared the remaining round buttons across the chat composer, conversation list, settings, and file picker, so they match the square controls already beside them.

## 0.27.0 - Note Graph & Compact Views

### Highlights

See your notes as a connected graph, fit more into every list with the new compact views, and watch thumbnails appear the moment you upload.

### Notes

- New Graph view in the notes sidebar. It shows your notes as a network: each note is a node, each Markdown link from one note to another is a connection, and notes with no links still appear so nothing is hidden. You can switch between just your notes and everything you can see, search to highlight matching notes, and tell favorites, journal entries, and regular notes apart by color. Click a node to open the note.
- The sidebar's "All notes" shortcut is now "My Notes" and lists only the notes inside your Notes folder and its subfolders, leaving out your daily journal entries. Notes kept elsewhere are still reachable by browsing to their folder.
- New "Compact note list" toggle in the notes preferences: each row collapses to a single line (title and favorite star), roughly halving its height so more notes fit on screen. It applies instantly and is remembered across sessions.
- Editing a note no longer shows up twice in the activity feed, and notes are counted once again in the dashboard and profile stats.

### Files

- The file browser footer now adapts to what you are doing: it shows the combined size while you have files selected, switches to "N of M items" while a search or type filter is active, and otherwise shows the usual counts and total size. A new info button next to it opens the current folder's properties panel (click it again to close).
- New "Compact file list" toggle in the files preferences: the list view uses denser rows so more files fit on screen. It applies instantly, persists across sessions, and leaves the mosaic view unchanged.
- Image thumbnails now appear right after a file is uploaded or replaced, instead of waiting up to a few minutes for the periodic scan, and they are generated faster for large photos.
- Squared the remaining round buttons (the favorite star and the "more" menu) in both the list and mosaic folder views, so they match the square controls already beside them.
- Files extracted from a ZIP archive now show their real size. They were previously listed as 0 bytes (the file still opened fine, only the displayed size was wrong).

### Chat

- Quick reactions are now personal: the emoji bar in the message hover toolbar shows the reactions you have used most over the last month, topped up with the defaults so it always offers six. It updates as soon as you react.
- An emoji you have already reacted with now shows as selected in the hover toolbar, matching the reaction bubbles under the message.

### Mail

- You can now turn off AI auto-labeling for a specific folder from its right-click menu, while keeping event detection and your own rules running. Handy for folders where automatic labels are just noise.
- Fixed bcc recipients being dropped when saving a draft. They are now kept, so reopening the draft still shows everyone you addressed.

### Calendar

- The activity feed now shows when an event actually takes place, not just when it was added. All-day events show the date; timed events show the date and time.

### Modules

- Deactivated modules no longer appear as greyed-out tiles on the home page; they are simply left out.
- Modules under active development can now be marked as "preview" and shown only to a chosen audience (for example staff only), so a self-hosted instance can try new modules out without exposing them to everyone.

### Performance

- The app feels a bit snappier and uses less memory, a noticeable win on small self-hosted machines.

## 0.26.0 - Mail Rules & UI Polish

### Highlights

Run a mail rule against messages you already have, not just new arrivals, alongside a more consistent button style and a handful of fixes across mail, files, and chat.

### Mail

- Apply a rule to an existing folder, straight from the rules list. Until now rules only ran on newly arrived messages, so a rule you created after a message had arrived never touched it. Now you can run any rule against a folder's existing messages: a preview first shows how many would match, then you confirm to apply. Works even on a disabled rule, since you are triggering it on purpose.
- Editing a rule now shows the condition's real field and operator again. Reopening a saved rule could display the wrong values (falling back to "From / contains") even though the rule itself was unchanged; the editor now fills in the values you actually saved.

### Files & Notes

- Opening a file from the activity feed now lands in the folder where the file lives, with the viewer open, instead of dropping you at the files root.
- Fixed two Markdown viewer glitches: an empty popup that briefly flashed in the top-left corner when opening a note, and a stray background behind the scroll area.

### Chat

- Long answer options in an "ask user" prompt now wrap cleanly on narrow mobile screens instead of overflowing their button and pushing the check mark onto its own line.
- The desktop send button now lines up with the message input column.
- The attach-file menu stays on screen instead of being clipped at the edge.

### Profile & UI

- Squared the remaining round action buttons across the mail and notes sidebars, their list headers, the chat composer, and the chat message toolbar, so they match the square buttons already used next to them. Modal and toast close buttons get the same rounded-square treatment.

## 0.25.0 - Note Linking & Faster Dashboard

### Highlights

Notes can now link to each other Obsidian-style: type `[[` in the Markdown editor to find and insert a link to another note. The dashboard also feels noticeably faster, painting right away and streaming in your activity afterwards, along with a couple of fixes to note filing and global search.

### Notes

- Link your notes together by typing `[[` in the Markdown editor: a search box opens, you pick a note (with the mouse or the keyboard), and a link to it is inserted right where you are. Available in both the Notes and Files apps.

### Performance

- The dashboard home page appears immediately instead of waiting on the recent-activity feed. The page paints first, then the feed streams in with a loading skeleton; if it cannot load, a Retry button is shown instead of a blank card.
- The usage-stats panel (file counts and sizes, message and note counts, ...) on the dashboard and profile loads faster, and revisiting either page within a minute is near-instant.

### Fixes

- Short Markdown notes (for example, one that contains only a heading) are no longer misfiled as plain text. They show up in the notes browser and in Markdown-type searches again, and notes that had already slipped out are restored automatically.
- Clicking a file in the global search results now opens it directly in the viewer, instead of only taking you to its folder.

## 0.24.0 - Attachments & Smart Files type detection

### Highlights

Attach files you already have to chat messages and emails with no re-uploading, and let file types be detected from real content so everything opens in the right viewer even when the extension lies.

### Files & Sharing

- Attach workspace files to chat messages and emails directly: a new picker lets you browse folders, search, and select several files at once without re-uploading them. Attached files are copied, so they stay available even if you later delete the original.
- Right-click a .zip file and extract its contents into a folder of your choice.
- Smarter file type detection: types are now recognized from a file's actual content rather than just its name, so files open in the correct viewer and show the right icon even when the extension is wrong or missing. You can also search and filter files by type.
- File and folder lists now sort by name case-insensitively, so names order naturally instead of grouping all the capitalized ones first.

### Chat

- New compact mode, with independent toggles for the conversation list and the message view, set from a preferences popover in the sidebar. The compact list fits about 4-5 more conversations on screen, and both densities persist per user.
- AI bots can now offer clickable answer suggestions: when a bot asks a question with a few likely answers, it can present 2-6 buttons, and tapping one sends it as your reply. In group chats, everyone sees which option was chosen.
- Fixed minor visual glitches in the compact list: reply quotes now align with media embeds, and avatar status rings no longer overlap.

### Mail

- The single "Enable AI features" switch is now three independent toggles: automatic classification, event extraction, and on-demand actions (summarize, compose, reply). Turn on only the ones you want; your existing preference carries over until you change it.

### Command Palette

- New quick actions for notes, files, and the dashboard, reachable straight from the command palette.
- "Open today's journal" jumps to today's journal note, creating it if it does not exist yet.

### Fixes

- Fixed a security issue where specially crafted file names, titles, mail subjects, contact names, or AI summary content could run scripts in another person's browser when shown in global search results or AI summaries.
- "Open in Files" from a note (toolbar or right-click) now opens the note in the files viewer and lands in its folder, instead of dropping you at the files root. Clicking a file in the activity feed now opens it too.
- Short or extensionless Markdown notes now open in the Markdown viewer instead of the plain-text viewer or failing to open.

## 0.23.0 - Mail Rules & Faster Pages

### Highlights

Mail gains a full filters and rules engine: set conditions on incoming messages and automatically label, move, star, or delete them. The rest of the release is a broad round of performance work, from video playback and large folder downloads to image caching and first paint.

### Mail

- New filters and rules engine. Per-account rules with conditions on sender, recipient, subject, body, folder, attachments, star, and date; actions to mark read/unread, star/unstar, add or remove a label, move to a folder, or delete. AND/OR groups, regex matching, and a "stop processing more rules after this one" flag. Manage everything from a per-account dialog with reorder controls and an enable/disable toggle per rule.
- Right-click a message and pick "Create filter" to open a new rule pre-filled with that sender as the condition; tweak the action and save.
- Right-clicking a message from the "All inboxes" view now correctly shows the labels of that message's account; previously the labels submenu was empty.

### Calendar & AI

- Email-based event extraction now anchors relative dates ("next Friday", "tomorrow at 9", ...) on the date the message was sent, not on today. Old emails no longer produce calendar entries placed in the present.

### Files

- Video files now stream and seek inside the player: jumping ahead in a video no longer redownloads from the start. The same fast-seek support extends to attachments and shared-link previews.
- Bulk and full-folder ZIP downloads no longer load the whole archive in memory before sending. Multi-gigabyte folders now download with constant RAM, so large exports work on smaller deployments too.

### Performance

- First paint of every page is faster: Tailwind and DaisyUI are now bundled and served locally instead of pulled from a CDN, with only the classes actually used shipped to the browser.
- HTTP responses use a faster compression layer, so pages and API replies come down quicker across the board.
- Avatars and thumbnails are cached with stale-while-revalidate: revisits reuse the already-displayed image instantly while a fresh version loads in the background.
- Avatar images now lazy-load (only fetched when they scroll into view) and the Lucide icon library loads after the main content, so the initial page is lighter.

### Fixes

- Pages with a fixed-height navbar no longer scroll in the background when an inner panel scrolls: the page is locked at the html level so only the intended area moves.

## 0.22.0 - Calendar AI & Onboarding

### Highlights

AI bots now read confirmed bookings, meetings, and tickets straight out of your inbox and add them to your calendar, each with a short rationale and a confidence score. New users also get a guided welcome tour on their first sign-in.

### Calendar

- New AI-powered event extraction reads confirmed events (flights, meetings, restaurant bookings, medical appointments, concert tickets, ...) out of email threads and adds them to your calendar. Each suggested event carries a short reasoning line and a confidence score; vague proposals and marketing fluff are filtered out.
- Subscribed external calendars no longer re-write unchanged events on every sync, so refreshes complete faster and put less load on the server.

### Onboarding

- New users see a guided welcome tour the first time they log in, with quick walkthroughs of the core features. Existing users are not affected.

### AI Chat

- When a bot's first reply comes back empty and triggers an automatic retry, the retry no longer accidentally repeats earlier actions (saving a memory, sending a scheduled message, generating an image, ...). Each action now runs at most once per turn.
- Follow-up replies in a long chat come back faster: the stable part of the system prompt is now reused turn-over-turn instead of being reprocessed every time.

## 0.21.0 - Theme Picker & Reliability

### Highlights

The rebuilt theme picker keeps a separate light and dark theme, so the navbar sun/moon toggle swaps between your two favourites instead of resetting to the defaults, now with 26 themes to choose from.

### Themes

- The Preferences page now has two grids - one for your light theme and one for your dark theme - each with an "Active" badge marking the slot currently in use
- The navbar sun/moon toggle bounces between those two slots instead of resetting to plain light / dark, so picking Nord + Dracula (or any other pair) actually sticks across toggles
- Changing a slot in Preferences applies immediately, with no page refresh needed
- Expanded theme list, balanced at 13 light and 13 dark options: added Bumblebee, Retro, Valentine, Garden, Pastel and Lemonade on the light side, plus Synthwave, Halloween, Aqua, Black, Luxury, Business, Coffee and Dim on the dark side

### Fixes

- Self-hosted instances running on SQLite no longer hit intermittent "database is locked" errors when changing settings in quick succession (most visible when clicking through themes)
- Saving multiple preferences in a row is faster and consumes a single request instead of one per key

## 0.20.1 - Release Awareness

### Highlights

The "What's new" modal now opens by itself the first time you visit after a new release, and flags which versions you have already read.

### Changelog

- The "What's new" modal opens once automatically after every release that adds entries to the changelog
- The version sidebar inside the modal shows a coloured dot next to each release: highlighted when you have not seen it, muted once you have
- Scrolling past a version, or clicking it in the sidebar, flips its read indicator straight away

## 0.20.0 - PostgreSQL & Activity

### Highlights

PostgreSQL becomes a first-class database, with a tool to migrate an existing workspace off SQLite without losing data. Files also gain a per-file activity timeline, and several long-standing duplication bugs in calendar sync and scheduled messages are fixed.

### Database

- New `migrate_to_postgres` management command and step-by-step guide to move an existing workspace from SQLite to PostgreSQL with all data, history, and uploaded files intact
- PostgreSQL is now a supported and documented target for production deployments

### Files

- New activity timeline in the Properties panel shows every event for a file: who created it, who renamed it, who shared it, when it was moved, and more
- Right-click on a file inside a multi-selection now applies the chosen action (delete, cut, copy, download, favorite, pin) to the entire selection instead of just the file under the cursor
- Right-clicking a file that isn't part of the current selection collapses the selection to that file, matching standard file-manager behavior
- Properties sidebar no longer squeezes the page header off-screen on narrow viewports; it now slides over the file list as a full-coverage panel on mobile

### Calendar

- Subscribed external calendars no longer create duplicate events when two sync runs overlap

### AI Chat

- Scheduled assistant messages no longer get dispatched twice when more than one worker picks them up at the same moment

### Profile

- Tighter spacing and a cleaner activity heatmap layout on mobile

### Chat

- No more sidebar flicker on the first load on mobile

### Fixes

- Avatar uploads with unsupported or corrupted image data now return a clear error instead of a server crash
- URLs with malformed UUIDs return a clean 4xx error instead of a 500

## 0.19.0 - Stability & Polish

### Highlights

A reliability and polish release: mail is hardened against IMAP failures so moves, drafts, and accented folders all behave, file operations are safer, and pages load faster across the app.

### Mail

- Moving messages no longer risks losing mail when the IMAP server returns a failure mid-operation
- Saving a draft no longer risks overwriting the previous version on a partial IMAP failure
- Drafts deleted locally no longer reappear after a refresh when the IMAP delete fails
- Folders with accented characters now rename correctly instead of creating ghost folders
- Folder sync errors are now visible per-folder instead of being hidden when others succeed
- Zero-byte attachments are now preserved
- Unread counts on labels stay in sync after every batch action and folder mark-as-read
- Network errors during account or message actions no longer leave dialogs in a locked state
- Quick selection changes no longer briefly show the previous folder's messages or another contact's autocomplete results
- Trying to hide a special folder no longer leaves it half-renamed
- Saving a mail attachment to Files handles missing original blobs cleanly instead of returning a server error

### Files

- File locks behave correctly under concurrent acquire attempts
- Only the lock holder can release a file lock
- Copying files and folders is more memory-efficient and reliable for large content
- Copying or moving a file no longer briefly widens its access while the operation is in flight
- Moving a file or folder over WebDAV now relocates the underlying content alongside the metadata, instead of leaving the bytes at the old path
- Replacing an image's bytes through the API regenerates its thumbnail instead of serving the stale one
- Legacy root-level "Journal" folder is migrated correctly into the Notes hierarchy on first load, with no orphaned notes
- AI image edits with malformed input return a clear error instead of crashing the request

### Chat

- Direct messages and group conversations now share a single recency-sorted list in the sidebar
- Reactions, edits, link previews, pins, and read receipts no longer jump the message view to the top while you're scrolled up
- Switching conversations while messages are loading no longer briefly shows the previous conversation's messages
- @mention with no matches no longer swallows Enter when sending a message

### Dashboard

- Upcoming events widget now includes all-day events and events already in progress
- Upcoming events widget loads with a skeleton placeholder so the dashboard renders sooner
- Show or hide the upcoming events widget from your preferences

### Changelog page

- Redesigned with a vertical timeline and per-version titles
- Sticky version navigation on the side, with the active version highlighted as you scroll between sections

### Performance

- Faster listings and notification queries
- Smoother live updates with longer-lived connections and fewer reconnect blips
- Theme and timezone load with the page, removing the brief flash of the default theme on first paint
- Calendar, Files, and dashboard pages open faster on first load

### Security

- Malformed UUIDs in URL parameters now return a clean 4xx instead of a 500 error
- User-controlled values are sanitized before logging to prevent log injection
- File uploads use stricter file system permissions

## 0.18.0 - Performance & Reliability

### Highlights

Listings, sidebars, and notifications are noticeably faster and large WebDAV uploads hold up on slow networks, while new personal API tokens let you connect third-party apps and scripts to your workspace.

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
- Smoother refreshes of the sidebar, read receipts, and list updates - interactions no longer reset state mid-action

### Files & Notes

- Rename and action buttons now match the backend rules - the UI only offers what will actually succeed
- Journal notes can no longer be renamed by mistake
- File name validation blocks invalid characters before save
- Properties panel, pinned folders, and group sidebar refresh without flicker

### Profile & UI

- Refresh button added to the profile activity feed
- Generic help dialog with collapsible sections for cleaner navigation

### Fixes

- Multi-step operations are now fully transactional, preventing rare partial updates
- User settings are no longer fetched for anonymous visitors

## 0.17.0 - Calendar Overhaul

### Highlights

The calendar gets infinite scroll and a smoother mobile experience, while WebDAV grows more reliable on Windows and under concurrent uploads.

### Calendar

- Infinite scroll across events - no more pagination arrows
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

## 0.16.0 - Profile & Rich Media

### Highlights

**Profile customization** arrives with bio, role, and banner palette, and chat gains rich media: **link previews**, a **shared media gallery**, and AI-readable **video attachments**.

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

## 0.15.0 - External Calendars & Group Folders

### Highlights

**Subscribe to external calendars** (ICS) with automatic background sync, and share files across teams with new **group folders**.

### Calendar

- **Subscribe to external calendars** (ICS) with automatic background sync
- Action buttons on events from external calendars
- Recurring events from ICS now honor the repeat-count limit correctly

### Chat & AI

- **Rolling conversation summaries** keep AI context within limits while preserving long-running discussions
- AI tool call history now persists across sessions
- Empty AI summaries no longer break conversation updates

### Files

- **Group folders** - shared folder spaces with creation dialog and sidebar integration
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

## 0.14.0 - Notes & Unified Inbox

### Highlights

**Notes**, a new Markdown note-taking app with tags, filters, and folder-tree organization, joins the workspace, alongside a **unified inbox** for mail and a workspace-wide **Favorites** view.

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

## 0.13.0 - File Sharing Links

### Highlights

**Share files with anyone** via password-protected, expiring links. Mail gets smarter - automatic detection of deleted or moved messages, and cleaner AI classification for sent and draft folders.

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

## 0.12.0 - AI Search & PWA

### Highlights

AI gains **web search**, **scheduled messages**, and dedicated search across calendar, chat, mail, and files. Mail adds **AI-powered labels**, and the app becomes installable as a **PWA** with offline caching.

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

## 0.11.0 - AI Bots Overhaul

### Highlights

A major **AI bots** overhaul: bots now remember context, mention users, search the workspace, and generate images, with fine-grained access controls. Chat also gains **drafts**, **@mentions**, and syntax highlighting.

### AI & Bots

AI tools ecosystem and bot management overhaul.

- **AI Memory** - bots remember context across sessions, with search and filter UI
- **Image generation and editing** tools for bots
- **Workspace search tool** - bots can query across all modules
- Dedicated Mail, Files, and Chat tools
- Message search and user info retrieval tools
- **Bot access controls** - public visibility settings and capability flags
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

## 0.10.0 - AI Assistant

### Highlights

**AI Assistant** lands across Chat and Mail - bots respond in conversations (text and images), summarize emails, and help you compose replies. Mail adds **OAuth2 authentication** for providers like Gmail and Microsoft.

### New: AI Assistant

AI-powered assistant integrated across Chat and Mail modules.

- Configurable AI bots with a picker modal and per-conversation assignment
- **Chat AI** - bots respond in conversations with text and image attachments
- **Mail AI** - email summaries with a dismiss option, preserving formatting
- **Mail AI** - reply assistance using your sender identity for tone
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

## 0.9.0 - Polls & File Locking

### Highlights

**Calendar polls** let you schedule events democratically: propose time slots, invite guests (even without an account), collect votes, and pick the winner. Chat adds an **emoji picker**, and **file locking** prevents concurrent editing conflicts.

### Calendar

- **Poll scheduling** - create polls with time slots, invite guests via shareable link, collect votes, pick the final slot
- Edit polls by adding or removing slots; redesigned poll list with search and filters
- Optional notifications when guests vote on your polls
- **iCalendar email integration** - incoming `.ics` attachments are processed and replies sent automatically
- Event-specific URLs in notifications for direct navigation
- Pending actions now include events until end of day
- Invitation calendar name updates when your account display name changes

### Chat

- **Emoji picker** for messages and reactions
- Messages appear immediately with a loading animation - no waiting for the server
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

## 0.8.0 - Replies & Read Receipts

### Highlights

Chat gets **message replies** with quoted preview and click-to-scroll, plus **read receipts** with detail popovers. File and calendar activity now produce dedicated notifications.

### Chat

- **Reply to messages** with a quoted preview - click the quote to scroll to the original
- **Read receipts** with double-check indicators, per-group read count, and a detail popover
- Message timestamps moved to the group footer alongside read receipts

### Notifications

- **File activity notifications** - edits, shares, permission changes, deletions, and comments
- **Calendar event notifications** - invites, updates, cancellations, and RSVP responses
- Notification URLs and click handling now work reliably

## 0.7.0 - Notifications & Presence

### Highlights

A real-time **notification system** lands with its own UI panel. User **presence tracking** (online, away, busy, invisible) shows you who's around, with DM shortcuts from profiles and user cards.

### Notifications

- **Notification system** with a dedicated UI panel and real-time delivery

### Users

- **Presence tracking** - online, away, offline detection
- **Manual status** - online, away, busy, invisible
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

## 0.6.0 - Mail & Recurring Events

### Highlights

**Mail**, a new IMAP/SMTP client with account auto-discovery, drafts, and drag-and-drop folders, joins the workspace, and the calendar gains **recurring events** with scope-aware editing.

### New module: Mail

IMAP/SMTP mail client integrated into the workspace.

- Account setup with **auto-discovery** of IMAP/SMTP settings
- Compose dialog with reply/forward detection, drafts, and attachments
- **Hierarchical folder tree** with subfolders, move, and drag-and-drop
- Customize folder icons and colors
- Filter messages by search, unread, starred, or attachments
- Drag-and-drop or context menu to move messages
- Contact autocomplete with popover cards
- **"Save to Files"** - save mail attachments directly to the file browser
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

## 0.5.0 - Agenda & Attachments

### Highlights

The calendar introduces an **Agenda view**, chat now supports **attachments** you can save straight to Files, and files get a **comments** system.

### Calendar

- **Agenda view** - chronological list of events across your calendars
- Event context menu with quick actions (edit, delete, duplicate)
- Show or hide declined events
- Smoother loading of the event detail panel
- Fixed all-day event formatting during event creation

### Chat

- **Message attachments** - upload and attach files to messages
- **"Save to Files"** - save chat attachments directly to your file browser

### Files

- **Comments on files** - add, edit, and delete
- Refreshed properties panel

### Users

- User mini profile popover when hovering avatars

### Infrastructure

- **Docker images** now published on GHCR for each `main` push and tag

## 0.4.0 - Chat, Calendar & Sharing

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
- Shared dialog utilities: confirm, prompt, message, error - with icons

### Infrastructure

- **Trash auto-purge** - trashed items are now periodically cleaned up

## 0.3.0 - Folder ZIP Downloads

### Highlights

Folders can now be **downloaded as ZIP archives**. A new "Download as ZIP" option appears in folder context menus, and the download endpoint transparently handles both files and folders.

### Files

- **Download folders as ZIP archives** - new "Download as ZIP" context menu option
- The download endpoint now handles both files and folders

## 0.2.0 - PostgreSQL Support

### Highlights

**PostgreSQL support** - the workspace can now run against PostgreSQL as well as SQLite. Monaco editor's base theme is now in sync with the workspace theme.

### Infrastructure

- **PostgreSQL support** for production deployments

### UI

- Monaco editor base theme syncs with the workspace theme

## 0.1.0 - Initial Release

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
