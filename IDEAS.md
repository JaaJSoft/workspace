# Workspace - Ideas & Roadmap

Your workspace, all in one place.

This file is the forward-looking roadmap, sorted by priority: the structural projects, the features worth planning and the technical work behind them. Shipped work is recorded in [CHANGELOG.md](CHANGELOG.md). Bugs and quick wins live in the issue tracker, not here. Items here get checked off and pruned as they ship.

**Positioning:** a self-hosted productivity suite for solo users and small teams. Every module must work great for one person and get richer with more users. From a Raspberry Pi to a Kubernetes cluster.

**Working rhythm:** one structural project at a time (a cross-cutting foundation or a module overhaul), with quick wins on existing modules alongside. The AI thread runs through everything: each module ships its AI tools with it.

**Reading the items:**
- **Effort:** S, M or L measures scope and risk, not duration:
  - **S:** a contained change in one module, with no new model and no design decision.
  - **M:** a feature or a refactor within one module, with model or API changes, new UI and their tests.
  - **L:** a new subsystem, a change across several modules, a data or format migration, or a design decision to settle first.
- **Issues:** `#123` is the issue that tracks an item.
- **Source:** the file was rewritten from a full audit of every module against v0.45.0 (November 2026).

---

## Priorities at a glance

| Priority | Structural projects | Module highlights |
|---|---|---|
| **P0 - Now** | Clean-up release: the open bugs, starting with #1618-#1626 and the Mail data issues (#1504, #1505, #1513, #1514), and housekeeping | The quick wins filed as issues |
| **P1 - Next** | Instance email; sign-in hardening; backup and restore; real-time v2; split Celery queues | Calendar reminders, Mail send queue, Projects "My work" |
| **P2 - Then** | Team spaces (spaces on groups, chat channels, group calendars, folder sharing, guests); file versions | Photos memories and Live Photos, Notes knowledge pack |
| **P3 - Later** | Event journal (audit log, automations, webhooks); AI à la carte and MCP; migrations and account export | Module backlogs below |
| **Someday** | Music & Audio, Bookmarks, RSS/Feeds, native desktop integration, JMAP, end-to-end encrypted mail; far off, a video library | |

---

## Cross-cutting projects

### Instance email - P1

The instance sends no email of its own: calendar invitations go out through the organizer's mail account, and nothing else can reach a user outside the app. This one foundation unlocks most of the trust backlog.

- [ ] Instance sender (SMTP from the environment, a Celery task, templates rendered in the recipient's language) and a canonical `PUBLIC_URL` for links built outside a request - #916, #925 (PR #1175)
- [ ] Password reset by email - #926
- [ ] Email verification and security alerts (new sign-in, token created, password changed) - #927
- [ ] Invite a user by email - #952
- [ ] Notification email channel and daily or weekly digest - #928
- [ ] Outbound ICS invitations to external attendees, from the organizer's account or the instance - #921, #930
- [ ] Email a share link to an external recipient - #929
- [ ] Poll invitations and booking confirmations by email - #905

### Sign-in and trust - P1 (hardening), P2 (teams)

- [ ] **Rate limiting** - covers `/login`, REST Basic auth, DAV and share-link passwords, per IP and per identifier - #997 (S/M)
- [ ] **TOTP and recovery codes** - on `/login` and `/admin`. Requires dropping Basic auth from the REST API and moving DAV to app passwords, or the password alone bypasses the second factor - #920 (M/L)
- [ ] **Sessions and devices** - list, revoke and sign out everywhere. Needs a session → user index, since cache sessions cannot be enumerated (M)
- [ ] **Scoped API tokens** - DAV only, read only or per module, with last use and a default expiry (`TOKEN_TTL` is `None` today) (M)
- [ ] **Global audit log** - sign-ins and failures, tokens, public links, role changes, admin actions; built on the event journal - #917 (M)
- [ ] **Invitations and guest accounts** - single-use invitation links, then a guest role with restricted visibility. Today every account can list every user and group - #952, #915 (M/L)
- [ ] **SCIM 2.0 and complete OIDC** - provisioning and deprovisioning, staff from IdP groups, an OIDC-only mode. LDAP comes through the IdP rather than natively - #918 (M)
- [ ] **Offboarding** - deactivating an account revokes its tokens, sessions and public links, and hands its files, projects and calendars to someone else (M)

### Self-hosting and operations - P1

- [ ] **Backup and restore** - `manage.py backup|restore` (the `db_snapshot` copy, a blob manifest, retention), a nightly task and a "last backup" card in the admin. Document Litestream or restic for continuous backups (M)
- [ ] **All-in-one mode for a NAS or a Pi** - `PROCESS_TYPE=all`, with `SECRET_KEY` and VAPID keys generated on first start and kept in the volume, and a documented memory profile. The base of the Synology package (PR #1475); see #1619 (M)
- [ ] **Split Celery queues** - interactive, heavy and ml. With one queue, a long photo analysis can hold every worker while a bot reply waits (see #1628). A single-process `ml` worker on a Pi (S/M)
- [ ] **Health console** - covers:
  - worker and beat heartbeats, queue length, free disk, pending migrations;
  - configuration warnings (no Redis, `NUM_PROXIES`, VAPID, default secret key);
  - an opt-in "new version available".

  Each module declares its own health cards (M)
- [ ] **Module policies per instance and per group** - turn Mail off, reserve Vault to a group. Generalizes the preview visibility rule (M)
- [ ] **Instance settings in the database** - AI, module visibility, default language and theme, limits. The environment keeps priority and keeps the secrets (M/L)
- [ ] **Admin inside the app** - users, groups, quotas, module policies, health and backups, translated and delegable. The Django admin stays for raw data, without private content (#1672) (L)
- [ ] **Structured JSON logs** - with request ids propagated to tasks - #553 (S/M)

### Real-time v2 - P1

A browser runs one SSE stream for all its tabs, and every event still makes each stream re-poll its providers. That is fine for a few people, and it is the wall for chat channels and live boards.

- [ ] **Typed wake-ups** - a typing ping or a call signal should not re-run the whole chat poll for every recipient - #1722 (S/M)
- [ ] **Chat event journal** - read through the core mailbox, with targeted DOM updates instead of reloading the last 50 messages. Every kind of change reaches the other members, hard deletes included (#1698 is today's gap) (L)
- [ ] **Call signaling, presence and typing on the core mailbox** - fixes #1710 for good (S/M)
- [ ] **Live boards and calendars** - SSE providers for Projects and Calendar (M)
- [ ] **Mail push** - an optional IDLE service that triggers folder syncs, and a Mail SSE provider (L)
- [ ] **Dedicated ASGI process for SSE** - later; decouples SSE from the SQLite write lock (L)

### Team spaces - P2

Groups (`auth.Group`) are already the team behind group folders, chat groups, projects, albums and group address books, and OIDC syncs them. What they lack: a page, admins who are not superusers, and a calendar.

A space is that team made visible. Chat channels, a shared drive and boards in one place is what neither Slack nor Discord offers.

**Decision: spaces are groups.** There will be no separate Discord-style server with its own membership and roles. It would duplicate `auth.Group`, for an audience (hundreds of members per server) that SSE and SQLite do not hold. Real-time v2 comes first.

- [ ] **Space profile** - a 1:1 model on `Group` with admins, description and icon; members managed without a superuser (M)
- [ ] **Space page** - the group's folder, channels, projects, address book, albums and calendar, through a section registry like People's (M)
- [ ] **Group calendars** - Calendar is the only module groups do not reach (L, see Calendar)
- [ ] **Chat channels** - see the steps under Chat
- [ ] **Folder sharing with inheritance** - #539 (L, see Files)
- [ ] **Team tags** in Files and Notes (M)
- [ ] **Roles in group address books** (M)
- [ ] **Guest accounts** - #915
- [ ] **Unified people, group and contact selector**, owned by People - #624 (M)

### Event journal and automations - P3

- [ ] **Unified event journal** - an append-only table written in the same transaction as the action (actor, verb, object, module, audience). It feeds:
  - the activity feed (each provider re-reads `limit+offset` rows per page today);
  - the audit log and webhooks;
  - the email digest and the account export.

  (L)
- [ ] **Automations and webhooks** - triggers come from the journal and conditions are JSON. Actions come from the typed AI tool catalog, with the user's own rights. Mail rules become one case. Signed outgoing webhooks first, incoming ones (for n8n) next - #552 (L)
- [ ] **Event-triggered agents** - "when a mail from X arrives, a file lands in Y or a task becomes overdue, wake this bot with this instruction" (L)
- [ ] **Account export** - an export registry each module contributes to:
  - files as a ZIP, calendars as .ics, contacts as .vcf, notes as Markdown;
  - projects and chat as JSON, mail as .eml, the vault as an encrypted archive.

  Runs in a task, and Imports can read it back - #919 (L)

### AI à la carte - P1 (fixes) to P3

- [ ] **Per-bot tool sets, loaded on demand** - every bot receives all 56 tools today, roughly 12k tokens resent on every model call (estimate). Sets per module, ticked per bot, plus a "load a tool set" meta-tool. After #1622 - #797 (M/L)
- [ ] **System prompt built from the tools a bot actually has** - #796 (S)
- [ ] **Provider layer** - a `Provider` model (OpenAI-compatible, Anthropic, Ollama; URL, encrypted key, context window, prices), small, reading and vision roles per provider, native adapters with prompt caching (L)
- [ ] **MCP server** - expose the tool registry to external AI clients with personal API tokens (M). Then an MCP client for servers the admin declares (M/L)
- [ ] **Knowledge bases** - attach a folder or notes to a bot. Hybrid full-text + vector search on `common/vectors`, with answers that cite their sources (L)
- [ ] **Usage, costs and quotas** - covers:
  - a run that acts for a user (`acting_for`) and a price table;
  - daily token quotas per user or group, and a budget per goal (scheduling limits: #1659);
  - an AI tab in Settings › Usage.

  (M/L)
- [ ] **Agentic safety** - once a run has read external content (web, incoming mail, a shared file), every write goes through a bound confirmation. Bots' writes are logged. Builds on #1649 (M)
- [ ] **Evaluation bench** - `scripts/ai_bench.py` on the model of the photo benches. Scenarios replayed against a real model on demo data, scored on tool choice, arguments, refusals and tokens - #795 (M)

### Moving in and staying - P3

- [ ] **Finish the Nextcloud migration** - calendars, Deck, Notes, Login Flow v2 and stable `oc:fileid` ids (see Imports)
- [ ] **Google Takeout archives** - files, contacts, calendars and Google Photos metadata, with no OAuth app verification to go through
- [ ] **OAuth drives** - OneDrive and Dropbox first, Google Drive next - #696
- [ ] **Note vaults and chat history** - note vaults from Obsidian, Notion, Joplin and Evernote; chat history from Slack, Mattermost and Discord
- [ ] **Device sync** - sync-grade WebDAV, then a Nextcloud-compatible API subset so the Nextcloud desktop and mobile clients work against Workspace (see Files) - #909
- [ ] **Account export** - #919 (above)

### Frontend and accessibility

- [ ] **Accessibility** - covers:
  - axe-core in the e2e suite, one page per module;
  - a skip link and keyboard alternatives to drag and drop;
  - focus handling in dialogs, and live regions.

  #755, #756, #757 (M/L)
- [ ] **Global CSP in steps** - `object-src 'none'`, `base-uri`, `frame-ancestors` and `form-action` now. `script-src 'self' 'unsafe-eval'` once about thirty inline scripts have moved out (M)
- [ ] **Monaco → CodeMirror 6** - Monaco 0.45 is 11 MB of the 23 MB of vendored assets and handles touch badly. CodeMirror already ships with Milkdown Crepe (M)
- [ ] **Targeted offline** - recent notes readable offline, drafts, queued chat messages (L)
- [ ] **Search v2** - a full results page, filters, operators (`in:mail`), ranking across providers, and a time budget per provider (#1708) (M)
- [ ] **JS lint** - Biome with no-var and no-undef on `window.*` globals, plus tests for the remaining frontend rules (spread-mixin getters, string `:style` next to `x-show`) (S)

---

## New modules

### Bookmarks & Read-it-later - Someday

Contained scope, very "self-hosted". Reuses the chat link preview (OpenGraph), the files tag system and full-text search.

- [ ] Save URLs with title, description and tags, with automatic title, favicon and preview capture
- [ ] Collections and sharing
- [ ] Import from a browser (HTML bookmark file)
- [ ] Later: browser extension, offline page archive, dead link detection

### RSS / Feeds - Someday

A feed reader with Celery sync, reusing the feed parser of the AI tools (`ai/services/feeds.py`) and the ETag handling of ICS feeds.

- [ ] Subscribe to RSS/Atom feeds, in folders
- [ ] Read and unread states, starring
- [ ] Save an article to Notes or Bookmarks
- [ ] Later: OPML import and export, full-content extraction, a digest by email

### Music & Audio - Someday

A library over the music already in Files, the way Photos is one over pictures. There is nothing to import, a group folder makes a family library, and a playlist can be shared by link.

- [ ] **Library** - tags read with ffprobe, already in the media pipeline: title, artist, album, track and disc numbers, year, cover art, MusicBrainz ids. `mutagen` is GPL and excluded by the licence policy; `tinytag` (MIT) is the fallback. A `Track` 1:1 on `File`, albums and artists derived from the tags, and the library scopes of Photos (L)
- [ ] **Views and player** - covers:
  - albums as a grid of covers, artists, tracks, recently played;
  - playlists that are personal, of a group, or shared by public link;
  - a queue, shuffle and repeat, and Media Session controls on the lock screen and headsets.

  (M)
- [ ] **Playback across modules** - every module is a full page, so leaving Music stops the sound. Start with a player in a window of its own, before changing how the app navigates (M)
- [ ] **Subsonic API** - the OpenSubsonic subset (browse, stream, cover art, playlists, stars, scrobble), authenticated with API tokens. It brings mature mobile and desktop apps, with offline listening, gapless playback, CarPlay and Android Auto (M)
- [ ] **Later:**
  - on-the-fly Opus transcoding for mobile data (optional on a Pi);
  - ListenBrainz scrobbling and synced lyrics (LRC);
  - AI tools;
  - podcasts (with RSS / Feeds) and audiobooks, on the same playback engine with resume positions.

### Video library - Someday, far off

Movies and series as a media library, the way Jellyfin or Plex do it. That is a whole product (metadata scraping, adaptive transcoding, TV clients), heavy on CPU, and Jellyfin already does it well.

Personal videos stay in Photos and Files, where the work is making them play everywhere (see Photos) and the viewer's subtitles (see Files). Until a video library makes sense:

- [ ] Document a Jellyfin container that reads the same volume read-only (local storage only), and link it from the dashboard

---

## Module backlogs

### Chat

**Features**

- [ ] **Public channels and a directory** - browse and join, reusing member reactivation - #911 (M)
- [ ] **Create a task from a message**, and clickable `WR-42` references (M)
- [ ] **Polls** - `MessageInteraction` already reserves the kind; needs a votes table (M)
- [ ] **Send later and "Remind me"** for people, on the dispatcher bots use (M)
- [ ] **"What did I miss?"** - a private AI summary of unread messages, shown to the asker only, without adding a bot to the group (M)
- [ ] **@here and @group** - and @everyone reserved to admins past N members (S/M)
- [ ] **Activity inbox** - my threads, mentions, reactions received (M)
- [ ] **Voice message transcripts** between people (S/M)
- [ ] **Saved messages and forwarding** (M)
- [ ] **Conversation export** - Markdown or JSON with attachments, plus a configurable retention (M)
- [ ] **Search operators** `from:`, `in:` and `has:`, with attachment names in the index (S/M)
- [ ] **Do not disturb** that holds pushes, with quiet hours; see #1694 (S/M)

**Big projects**

- [ ] **Channels, in steps** (after real-time v2):
  1. Channel fields on `Conversation`: visibility, topic, posting policy, archived state, position. Every existing group becomes a private channel; direct messages do not change.
  2. Directory, join, leave and preview (#911), with the unified selector (#624).
  3. Sidebar sections (channels, direct messages, bots, personal sections) and an unread filter.
  4. @here, @channel and @group, with notification defaults by channel size.
  5. A rail of spaces: the user's groups.
  6. After an SFU: persistent voice rooms, and forum channels built on threads.

  What breaks on purpose:
  - the purge of left conversations;
  - the cascade delete with the group (archive instead);
  - the "reader = member" assumption, once previews exist;
  - per-message read receipts past N members.
- [ ] **Calls v2** - an optional SFU past 6 participants (LiveKit, as a separate service), persistent voice rooms, raised hands, camera and screen share together (L)
- [ ] **Meetings with external guests** - split PR #983: guests and `participant_key` on calls first, then a meeting chat that is a regular conversation - #912 (L)
- [ ] **Bots in group conversations** - a bot identity with its own rights, or replies only the asker sees. Same base as background research - #803 (L)
- [ ] **Attachments stored in Files** - in the group's folder, counted in quotas and searchable; see #1719 (L)
- [ ] **Import from Slack, Mattermost, Discord and Rocket.Chat** (L)

**Technical**

- [ ] **Indexes for the SSE poll** - `edited_at`, reaction and link preview dates (S)
- [ ] **Lighter conversation list** - annotate member counts instead of serializing every member, and filter `?q=` in SQL (M)
- [ ] **Shared helpers** - for the 28 copies of the "not a member" check and the 7 message querysets; split `info_panel.html` (S/M)
- [ ] **Archive rather than purge** abandoned conversations (S)
- [ ] **Coverage** - raise the 55% floor (S)

### Files

**Features**

- [ ] **Resume uploads after a reload** - deterministic upload ids + IndexedDB; the API already accepts a client-chosen id - #1448, #1450, #1451 (M)
- [ ] **RAR, 7z and tar** - plus browsing inside an archive, through libarchive - #1584 (M)
- [ ] **pdf.js viewer** - Android browsers cannot show a PDF inline. Adds search and page thumbnails, and makes public links usable on phones (M)
- [ ] **OCR for scans and pictures**, feeding full-text search - #938 (M)
- [ ] **Public links v2** - preview without download, the uploader's name on drop links, a notification on first access, expiry on user shares - #539 (part) (M)
- [ ] **Team tags** - no tag can be set on a group or shared file today, which also blocks group notes (M)
- [ ] **Search in a folder and its subfolders**, with type, date, size and owner filters (M)
- [ ] **AI write tools** - create, move, share, file an attachment. Auto-tag documents into existing tags (images are already tagged by Photos) - #544 (M)
- [ ] **Compress to ZIP on the server**, as a background operation (S/M)
- [ ] **Save from a URL** (S/M)
- [ ] **Subtitles and chapters** in the video viewer - `.srt` and `.vtt` files next to the video (S/M)

**Big projects**

- [ ] **Version history** - #545 (L):
  - versions stored outside the live tree, like the trash; a restore is an ordinary content write;
  - tiered retention, otherwise the notes autosave creates a version every 2 s;
  - WebDAV and WOPI writes included; a diff for text.
- [ ] **Folder sharing with inheritance and permissions v2** - inheritance through the indexed `path`; read, drop, edit and manage levels, with expiry. Reaches the access queries, WebDAV, search and Photos - #539 (L)
- [ ] **Desktop and mobile sync without an app of our own** - #909, an alternative to #559 and #560:
  1. **Sync-grade WebDAV:**
     - propagated folder ETags, `X-OC-Mtime`, `oc:checksums` (SHA-256 is already stored);
     - "shared with me" visible;
     - personal and group spaces separated at the root (this breaks existing mounts);
     - tested in CI with rclone.
  2. **A Nextcloud-compatible API subset** (status.php, Login Flow v2, OCS capabilities, `remote.php/dav`, chunking v2), so the Nextcloud clients work.
- [ ] **Mass uploads** - one request for many small files, rows created in bulk, one Celery task per file instead of two - #1519 (M/L)
- [ ] **Folders of 10,000+ entries** - server-side paging and sorting, virtualized rendering (M/L)
- [ ] **"Ask your files"** - chunked documents embedded with fastembed into `common/vectors`, exposed as an AI tool. Builds on chunked indexing (#1685) (L)
- [ ] **Folder rules** - "when a file lands in X: tag, move, notify", on `@on_file_event`. The first brick of automations (L)
- [ ] **Image annotation** (Someday)

**Technical**

- [ ] **Coalesce and expire `FileEvent` rows** - the autosave part is #1718 (S)
- [ ] **Split the largest files** - `ui/views.py` (1,487 lines), `FileSerializer` (645 lines), `table.js`, `browser.js` (M)
- [ ] **Invert the Files → Notes dependency** - journal rules are coded in Files; Notes should register a rename guard instead (S)
- [ ] **Coverage** - raise the 65% floor (S)

### Notes

**Features**

- [ ] **Templates** - a templates folder, "new note from template", a journal template with `{{date}}` variables, and an entry in the `/` menu - #555 (S/M)
- [ ] **Outline** - a headings panel with jump on click, and an optional TOC block - #557 (S)
- [ ] **Export to PDF and HTML** - a print stylesheet, and standalone HTML with embedded images - #558 (S/M)
- [ ] **Backlinks, then unlinked mentions** - `FileLink` is already indexed by target, but no UI shows it (S, then M)
- [ ] **Obsidian and Logseq compatibility** - resolve `[[Name]]` by name and relative `.md` links, and sync frontmatter tags with app tags. Today a vault dropped over WebDAV has no link in the graph (M)
- [ ] **Quick capture** - the share target accepts files only; take shared text and URLs into today's journal or a new note (S/M)
- [ ] **Journal and agenda** - today's events on top of the daily note, and a date picker (S/M)
- [ ] **Tasks in notes** - collect `- [ ]` items across notes, and turn one into a Projects task (M)
- [ ] **AI tools** - create a note, append to the journal, summarize (S/M)
- [ ] **Embeds on public links** - #1715 (M)

**Big projects**

- [ ] **Editing with several people** - #910:
  1. Presence, and a three-way merge on conflict instead of the "reload" banner (M).
  2. Yjs through the Milkdown collab plugin over SSE + POST, with the `.md` staying the reference (L).
- [ ] **History and diff**, once file versions ship (#545), with tiered retention (M)
- [ ] **Note vault imports**, with links rewritten (see Imports) (M/L)

**Technical**

- [ ] **Move the editor out of the Files template**, into a module testable with `node --test` (S/M)
- [ ] **Split `notes.js`** (1,558 lines) into mixins (M)
- [ ] **Coverage** - raise the 45% floor (S)

### Photos

**Features**

- [ ] **Memories** - "on this day" in previous years, at the top of the timeline and as a dashboard widget. One picture per burst; no hidden, sensitive or screenshot photos - #1167 (M)
- [ ] **Live Photos and Motion Photos** - pair the photo with its clip, hide the companion video and play it on long press. Skip the clip in the models (M)
- [ ] **Offline places** - local reverse geocoding (GeoNames cities500, licence to check). Gives a place in the viewer, a Places page, search by city and trip memories (M)
- [ ] **Combined search** - description search does not combine with person, tag, favorites, type or period. Filter first, then rank, and save the query as a smart album - #1283 (M)
- [ ] **Library clean-up** - exact copies and bursts: keep the best (resolution, size, sharpness) and trash the rest at once (M)
- [ ] **Contributing album by link** - guests drop photos without an account, on the capped upload mode of Files share links. Then album comments and reactions (M)
- [ ] **AI tools** - Photos has none: search photos (see #1668), create an album, add to an album (M)
- [ ] **More non-destructive edits** - exposure, contrast, saturation, temperature, straighten (M)
- [ ] **Learned tags as suggestions to confirm** - in a review like People's. The bench finds one photo in ten at 90% precision, too few to apply alone - #1299 (M)
- [ ] **Maps without leaking browsing** - a caching tile proxy, or local PMTiles (M)

**Big projects**

- [ ] **Videos that play everywhere** - an H.264/AAC copy made at low priority when the codec needs it. HEVC, AVI, MTS and WMV end on the download card today, even in public links. Optional hardware acceleration (L)
- [ ] **First-class family library in a group folder** - today, moving a photo into a group drops its faces, and auto tags only reach personal files. Per-group face grouping, with each member's consent (L)
- [ ] **Partner sharing** - share a whole library from a date, read by the Shared and All tabs (L)
- [ ] **Migration from Google Photos, iCloud and Immich** - Takeout JSON and XMP sidecars, and albums (L)
- [ ] **OCR of screenshots and documents** (RapidOCR) (L)
- [ ] **Precomputed People review**, maintained incrementally from corrections - #1401 (M/L)
- [ ] **Browsing 100k photos** - a month scrubber beside the grid, and virtualized pages (M/L)

**Technical**

- [ ] **A dedicated `ml` queue and a single-process worker** - each process loads its own models (about 2.5 GB with SigLIP 2), and searches time out behind video analyses (#1628) (M)
- [ ] **One decode per photo** instead of four (faces, silhouettes, embeddings, sensitivity) (M)
- [ ] **int8 models**, measured with the benches, and a published Raspberry Pi profile (M)
- [ ] **ML dependencies as an optional `ml` extra**, with a slimmer image (M)
- [ ] **Split the largest files** - `ui/views.py` (1,859 lines), `faces.js` (2,002) and `photos.js` (1,710); move the People services into `services/faces/` (M)
- [ ] **Library performance bench** at 100k photos, on SQLite and on a Pi (M)
- [ ] **e2e tests for the People review** (S)

### Mail

**Features**

- [ ] **Send queue** - undo send, scheduled send, retries, sending outside the request - #548 (M)
- [ ] **Conversation view** - a `thread_id` computed at sync from References and In-Reply-To (L)
- [ ] **Snooze and follow-ups** ("no reply within N days") (M)
- [ ] **Rich editor** - the chat's Milkdown composer, HTML signatures, pasted images (M)
- [ ] **Identities and aliases** - several From addresses per account, picked from the address a message was sent to (M)
- [ ] **One-click unsubscribe** (RFC 8058) and a Subscriptions view (S/M)
- [ ] **Remote images blocked by default** - "show images", trusted senders, an image proxy; builds on the isolated rendering of #1626 (M)
- [ ] **Mail to task, mail to event**, by hand (S/M)
- [ ] **Templates and AI-suggested replies** (S/M)
- [ ] **Accounts pre-configured by the admin**, created at first SSO sign-in - #924 (M)
- [ ] **History on demand** - only the last 200 messages of each folder are fetched today. Add "load older" and a low-priority backfill (M)
- [ ] **Out-of-office and server-side rules** through ManageSieve (L)

**Big projects**

- [ ] **IMAP sync v2 on maintained libraries** - IMAPClient for the protocol, the standard library's modern email API for parsing and composing - #1774:
  - one connection per pass, CONDSTORE/QRESYNC to skip unchanged folders;
  - MOVE and UID EXPUNGE;
  - a cadence per folder.

  (L)
- [ ] **Stable message identity** - labels, AI summary and extractions follow a message by Message-ID when it moves outside Workspace or UIDVALIDITY resets - #1504, #1505 (M/L)
- [ ] **Data lifecycle** - purge messages marked deleted, delete attachment files no row points to, and delete an account in a task instead of the request (M)
- [ ] **Attachments on demand** - fetch BODYSTRUCTURE and text parts at sync, and attachments when opened (L)
- [ ] **JMAP backend** (Stalwart, Fastmail) behind a backend interface, after sync v2 (L, later)
- [ ] **Encryption** - first show whether a message is signed or encrypted (S/MIME, PGP/MIME), then OpenPGP.js as a file of its own (LGPL) (L, low priority)

**Technical**

- [ ] **Indexed correspondents table** - replaces two 500-message scans per keystroke, and serves People (M)
- [ ] **Cursor pagination** instead of OFFSET + count (S/M)
- [ ] **Labels at user level** instead of per account (M)
- [ ] **Split and cover** - split `ai_tools.py` (843 lines) and `mail_folders.js`, and raise the 55% coverage floor (S)

### Calendar

**Features**

- [ ] **Per-event reminders** - offsets per event and defaults per calendar, in push and in-app, aligned on VALARM (kept today but never fired) (M)
- [ ] **Real invitations for external attendees** - send invitations and cancellations, and turn replies into attendee status - #921, #930, after instance email (L)
- [ ] **User sharing and group calendars** - free/busy, read and write roles; a team calendar like group address books (L)
- [ ] **Availability and "Find a time"** - a free/busy service with working hours, used by the event form, the AI, polls and booking pages - #905 (PR #1468) (M)
- [ ] **Built-in video** - "add a Workspace call" with a join button; detect Meet, Zoom and Teams links (M)
- [ ] **Invitations on phones** - a read-only "Invitations" calendar over CalDAV, or an RFC 6638 scheduling inbox (M)
- [ ] **Time blocking** - drop a Projects task on the grid to create a linked event (M)
- [ ] **Polls v2** - a deadline and reminders to non-voters, invitations by email, voters without an account added as external guests (S/M)
- [ ] **Resources and rooms** with conflict detection (L, low priority)
- [ ] **Sync with Google, Outlook and Nextcloud** through a CalDAV client and the Mail OAuth - #551, #743, #728 (L)

**Big projects**

- [ ] **One iCalendar reader** for CalDAV objects, subscribed feeds and emailed invitations, with the same fidelity everywhere; fixes #1623 - #1775 (M)
- [ ] **iTIP and attendees overhaul** - one attendee model mapped to ATTENDEE and ORGANIZER in CalDAV and ICS, and the full organizer flow. After #1775 and the reply fixes in #1657 and #1660 (L)
- [ ] **Sharing and availability foundation** - per-calendar rights, group calendars, free/busy. Booking pages plug into it (L)
- [ ] **FullCalendar 7** - with `calendar_events.js` split and drag and drop on. Evaluate EventCalendar (MIT) if rooms come in - #1102 (M/L)

**Technical**

- [ ] **Action registry for events** - rights are computed in JS today, and the menu is static (M)
- [ ] **SSE provider for Calendar** (S/M)
- [ ] **Shared DAV skeleton with People** - principal, root, multiget and sync, in `core` (M)
- [ ] **Split and cover** - split `ai_tools.py` (1,126 lines) and `_poll_modals.html` (740 lines), and raise the 75% coverage floor (S)

### Projects

**Features**

- [ ] **Recurring tasks** - "every Monday", "the 1st of the month", "3 days after completion", on the calendar's recurrence rule and picker (M)
- [ ] **Saved views** - personal or per project, pinned in the sidebar and shareable; filters already live in the URL (S/M)
- [ ] **Mail to task** - a button in the reader, a rule action, and a "task" kind next to event extraction (M)
- [ ] **Chat to task** - from a message menu, with the mentioned person as assignee and a message back on completion (S/M)
- [ ] **Time tracking** - entries, a timer in the panel, totals against estimates, a weekly export (M, on real demand)
- [ ] **Templates** - duplicate a project without its tasks; task templates with a checklist (M)
- [ ] **Read-only public link** to a board or a roadmap (M)
- [ ] **Move or duplicate a task** to another project (M)
- [ ] **Trash and undo** - deleting a task is final today, comments and attachments included (M)
- [ ] **Swimlanes** by assignee, epic or priority (M)
- [ ] **Import and export** - CSV or JSON export; CSV and Trello import (M)
- [ ] **AI for planning** - the update tool only assigns and changes due dates. Add priority, labels, epic, estimate and checklist, and sprint or week summaries from the event log (S/M)

**Big projects**

- [ ] **My work** - Inbox, Today, Upcoming and Overdue across every project, with a quick add that reads project and date from natural language. `/projects` only redirects to the last project today (M/L)
- [ ] **Live boards** - SSE fed by the services, targeted card or column refresh, presence (M)
- [ ] **Automations per project** - triggers (created, moved, overdue, checklist done) leading to actions (assign, label, move, notify, follow-up task), on `TaskEvent` and the action registry (L)
- [ ] **Custom fields** - text, number, date, select, URL; filterable and shown on cards (L)
- [ ] **Nextcloud Deck and Tasks import, then tasks over CalDAV** (iOS Reminders, Tasks.org, Thunderbird) - #729 (L)
- [ ] **Git forges** - GitHub, Gitea and GitLab issues ↔ tasks; "fixes WR-42" closes the task. After #729 - #463 (L)
- [ ] **Editable timeline** - drag and stretch bars, blocking arrows, dependents shifted along (L)

**Technical**

- [ ] **One task update service** - the update logic lives in the viewset, and the AI tool rewrites part of it (#1635). Prerequisite of mail to task, recurrence, automations, #463 and #729 (M)
- [ ] **Split the largest files** - `views/viewsets.py` (1,350 lines), `board.js` (2,181), `settings.js`, `settings.html`, `task_panel.html` (M)
- [ ] **Sparse positions** - a reorder rewrites every shifted task today (M)
- [ ] **Past 500 tasks** - load more per column, or virtualize, instead of asking to narrow the filters; the timeline also counts undated tasks in the cap (#1636) (M)
- [ ] **Cached reports**, instead of replaying the whole log on each visit (M)
- [ ] **e2e tests** for drag and drop (asserting the negotiated effects), sprints and bulk edit; then raise the 85% floor (S/M)
- [ ] **External id on tasks, and outgoing webhooks** (S/M)

### People

**Features**

- [ ] **Interaction history** - mails exchanged, meetings, conversations and tasks, through the section registry: a light CRM without a new model (M)
- [ ] **Duplicate detection and merge** - same email, phone or normalized name; the merge publishes the deletion in the CardDAV journal (M)
- [ ] **Birthdays** - a read-only calendar, also over CalDAV, and a line in the morning summary (S/M)
- [ ] **Other contacts** harvested from Mail, added in one click (M)
- [ ] **Team directory** - a read-only address book of the instance's accounts, over CardDAV (M)
- [ ] **Roles in group address books**, and sharing a personal one (M)
- [ ] **Keep in touch** - a wanted frequency, the last exchange, a reminder (M)
- [ ] **CSV import and export** for Google and Outlook (S/M)

**Big projects**

- [ ] **Unified selector** for accounts, groups, contacts, lists and mail history, owned by People. It replaces the calendar invitee selector, the mail autocomplete, the user selector and the group selector - #624 (M/L)
- [ ] **Light CRM** - interaction history, keep in touch, and organizations as entities (L)

**Technical**

- [ ] **Normalized address index**, for the mail card, invitees, duplicates and history (M)
- [ ] **Split `people.js`** (844 lines), and share the DAV skeleton with Calendar (S)

### AI

**Features**

- [ ] **Streaming replies**, over the existing `ai_stream` SSE channel (M)
- [ ] **Bots read attachments** - a PDF or a DOCX reaches the bot by name only today (S/M)
- [ ] **Assistants created by users** - prompt, model, tools, avatar, shared with a group. The access model exists; creation does not. After #797 (M)
- [ ] **Background research that reports back** into Notes - #803 (M/L)
- [ ] **People, Notes and Photos tools** - three modules shipped without them (S each)
- [ ] **AI in the Notes editor**, then in Projects (split a task, summarize a thread) (S/M)
- [ ] **Richer recurrences for scheduled messages** - working days, "first Monday", on the calendar RRULE (S/M)
- [ ] **One-click approval** - an Approve / Decline card runs the pinned action without going back through the model - #852 (M)
- [ ] **Manageable memory** - a global page and sorting, on top of the cap of #1663 (S/M)
- [ ] **Client-rendered pages**, through an optional rendering service - #810 (M)

**Big projects:** per-bot tool sets, the provider layer, the MCP server and client, knowledge bases, usage and quotas, agentic safety and the evaluation bench (see AI à la carte); event-triggered agents (see Event journal).

**Technical**

- [ ] **Split `tools.py` and untangle ai and chat** - `tools.py` (1,530 lines) becomes a package per provider. The goals, schedules and bots views move back from `chat/views/`, since ai and chat import each other both ways (M)
- [ ] **Context budget in tokens**, instead of 30 messages (M)
- [ ] **Run recording setting** (`AI_RECORD`: off / errors / full) - run records hold excerpts of the mails and files read, visible to admins for 90 days. Users see their own runs (S/M)
- [ ] **Bot tests** - node tests for `bot.js`, a bot e2e test, and a higher floor than 55% (S/M)

### Vault

**Features**

- [ ] **Restore a `.vaultarchive`** - the primitives are already in the bundle; a reader and a batch write endpoint are missing (M)
- [ ] **Import** - Bitwarden JSON first (the format the export writes), then browser CSV, 1Password (1PUX) and KeePass, parsed and encrypted in the browser (M)
- [ ] **Vault sharing** - #1189, #989 (L):
  - the vault key sealed to the recipient's verified key, and an owner-signed member list;
  - key rotation when a member is removed;
  - ownership transfer.
- [ ] **Share with a Workspace group** - a sealed key per member, and pending access for newcomers (M)
- [ ] **New entry types** - card, identity, SSH key, API token, Wi-Fi (M)
- [ ] **Password health report** - weak, reused, breached (opt-in k-anonymity check), no 2FA, `http://`; computed after unlock (M)
- [ ] **Scan a TOTP QR code** from the camera or an image (S/M)
- [ ] **Password history** per entry (M)
- [ ] **Unlock with a passkey** (WebAuthn PRF) (L)
- [ ] **Secure send** - a limited-use link, with the key in the URL fragment and an expiry (M)
- [ ] **Emergency access** (L)

**Big projects**

- [ ] **Out of preview, as one batch** - PR #1353 together with (M/L):
  - restore and import;
  - the French interface (#1546) and conflict-safe writes (#1673);
  - #989 and #990;
  - a threat model and a decision on custom fields.
- [ ] **Sharing and key rotation** - #1189 (L)
- [ ] **MV3 browser extension** - reuses the vault ES modules: restricted-token pairing, incremental sync, form filling, then passkeys (L)
- [ ] **Rollback and omission resistance** - a signed per-vault manifest (a monotonic revision and an entry digest), checked on load (L)
- [ ] **Offline reading** - an encrypted copy in IndexedDB, unlockable without network (M/L)

**Technical**

- [ ] **Batch write endpoint**, atomic and throttled (M)
- [ ] **Custom fields decided before they are exposed** - the label sits in clear and ASCII-only in `field_id`; an opaque id changes the format (M)
- [ ] **Sync API** - `?since=` or ETag, and paginated entries (M)
- [ ] **Split `vault_browser.js`** (1,844 lines) (M)
- [ ] **Document the threat model** (S)
- [ ] **Hybrid post-quantum KEM** for sealed keys, after sharing (L)

### Notifications

**Features**

- [ ] **Email channel and digest** - #928 (L, after instance email)
- [ ] **Settings › Notifications** - a type × channel matrix, named and revocable push devices, and do-not-disturb hours in the user's time zone (urgent still goes through) (M)
- [ ] **Security notifications** - new sign-in, API token created, password changed, vault opened on a new device (S/M)
- [ ] **Actions in push notifications** - Reply, Mark read, Accept (M)
- [ ] **Snooze a notification** (S/M)
- [ ] **ntfy, UnifiedPush or webhook channel** (M)

**Big projects**

- [ ] **Transactional outbox** - `notify` records an intent in the action's transaction. Rows, push, email and digest then go out after commit, with replay - #1447 (M)
- [ ] **Notification type registry** - each module declares its types (label, default channels, can be turned off). The chat, projects and mail levels become overrides (L)

**Technical**

- [ ] **A `kind` field now**, for the digest and filters (S)
- [ ] **Centralized multilingual rendering** - three implementations split recipients by language today (S/M)

### Dashboard

- [ ] **Widget registry** - each module publishes its widgets (fragment URL, size, default, preferences). The dashboard becomes a grid to hide, reorder and then drag, and stops importing four modules directly (M/L)
- [ ] **Resume** - the last opened files, notes and books, with their position. Needs open tracking in Files - #1489 (M)
- [ ] **Memories and birthdays** - #1167 (S/M)
- [ ] **Links to other services**, Homarr style, with an optional status check through the shared URL guard (#1625) (S/M)
- [ ] **AI morning brief** at local time (M)
- [ ] **Unified pins and favorites** across files, conversations and projects (M)

### Imports

**Features**

- [ ] **Calendars** - Nextcloud or any CalDAV server, plus a one-off .ics import - #728, #743 (M)
- [ ] **Generic CardDAV and CalDAV provider** - iCloud with an app password, Fastmail, mailbox.org, Radicale, Baïkal (S/M)
- [ ] **Google Takeout archives** - files, .vcf, .ics and Google Photos JSON sidecars (L)
- [ ] **OAuth drives** - #696, #744 (L):
  - OneDrive and Dropbox first;
  - Google Drive next (restricted scope, unverified self-hosted app);
  - Google Docs converted on import.
- [ ] **Nextcloud beyond files** - Deck to Projects (#729), Notes (#730), albums to Photos, file comments, shares between migrated accounts (M each)
- [ ] **Note vaults** - Obsidian, Notion, Joplin, Evernote, with links rewritten (M/L)
- [ ] **File tags from `oc:tags`** - #854

**Big projects**

- [ ] **Whole-instance Nextcloud migration by the admin** - accounts, groups and group folders through the provisioning API; files through a per-user Login Flow (L, exploratory)

**Technical**

- [ ] **Parallel transfers** - several readers, one writer (M)
- [ ] **Batched progress writes** (S)

### Users

- [ ] **Settings schema registry** - each module declares its keys (type, default, choices); closes #1667 and opens settings export and instance defaults (M)
- [ ] **Structured profile** - title, team, phone, working hours and pronouns as typed fields, reused by the user card, People and calendar availability (M)
- [ ] **Richer status** - message, emoji, expiry, quiet hours; "Busy" holding pushes is #1694 (S/M)
- [ ] **Directory and "My groups"** - accounts and groups with status, title, members and each group's resources. The user-side entry to team spaces (M)
- [ ] **Verified email and sign-in by email** - after #925 (M)
- [ ] **Account data in a 1:1 `UserAccount`** (verified email, MFA, profile) rather than swapping `AUTH_USER_MODEL`. Split `users/views.py` (971 lines) along the way (M)
- [ ] **Invert the `users` dependencies** - it imports `files` and `ai`; a registry of settings tabs replaces that (S/M)

### Platform engineering

- [ ] **Python 3.15** - greenlet, onnxruntime, pydantic-core and orjson had no cp315 wheels at the last lock. Add a non-blocking CI job to track them - #1145 (PR #1364) (S)
- [ ] **Django 6.2 LTS** (April 2027), as a stabilization target (S)
- [ ] **Consistent API v1** - three error shapes today (`detail`, `errors`, `error`) and about 80 untranslated messages. Move to RFC 9457 with `gettext`, then a client generated from OpenAPI (M)
- [ ] **CI targeted per module** - a dependency graph, where `common`, `core` and `users` reach everything; no double runs and grouped Dependabot updates (#1717) (M)
- [ ] **Coverage floors** - core and notes are at 45%; chat, mail and ai at 55%; files at 65%. Measure, then raise (S each)

**Files to split** (lines, excluding tests):

| File | Lines |
|---|---|
| `projects/.../board.js` | 2,181 |
| `photos/.../faces.js` | 2,002 |
| `photos/ui/views.py` | 1,859 |
| `vault/.../vault_browser.js` | 1,844 |
| `photos/.../photos.js` | 1,710 |
| `files/models.py` | 1,704 |
| `notes/.../notes.js` | 1,558 |
| `ai/tools.py` | 1,530 |
| `files/.../table.js` | 1,522 |
| `files/ui/views.py` | 1,487 |

---

## Decisions and deferred

- **Discord-style servers - no.** Spaces are groups (see Team spaces).
- **Zulip-style topics - no.** Threads and forum channels cover the need without redoing the message flow.
- **Bitwarden API compatibility - no.** It would keep a second copy of every entry under a weaker scheme. Interoperability goes through Bitwarden JSON import and export.
- **Nested subtasks - no.** Checklists cover them, and a checklist item can become a linked task.
- **Shared dashboards, custom KPIs, report export - dropped.** Reports live in Projects.
- **Deal pipeline and custom fields in People - dropped.** Out of scope for solo users and small teams.
- **Time tracking - on real demand only.**
- **Snippets and code - folded into Notes.** Code blocks with highlighting, tags and search already exist there.
- **Forms and surveys - last.** Furthest from the core use case.
- **Native desktop integration** (#559, #560) - waits for sync-grade WebDAV and the Nextcloud-compatible API, which may make it unnecessary.

---

## Housekeeping

- [ ] **Long-open PRs** - land or decide:
  - #1175 first: it unlocks instance email;
  - #1412 and #1475;
  - #1468, on the availability service;
  - #983, to split;
  - #1353, with the Vault batch;
  - #1188.

  Close #103: the Vault supersedes it.
- [ ] **Pending dependency updates** - cropperjs 2 (#1499, a major), FullCalendar 6.1.22 (#1500, #1501), luxon (#976), prosemirror-view (#1219)
- [ ] **Leftovers of #554** - #1546 (Vault), the admin labels and the API messages
- [ ] **Keep this file honest** - re-audit it against the codebase when preparing each release
