# Changelog

## 0.38.0 - Sprints, Office & Voice

### Highlights

Projects grows up: work in sprints, group tasks under epics, break them into checklists, link what blocks what, and estimate the effort. Office documents now open and save straight in the browser, and the assistant finally does real research - it reads several pages to answer one question, searches with proper filters, opens PDFs and feeds, and can even reply out loud.

### Projects

- Sprints and a scrum board. A project can now run in iterations: plan a sprint from the backlog, start it, and the board shows only that sprint's work. Complete it and you choose what happens to the unfinished work - back to the backlog, or carried into another sprint where it resumes exactly where it stopped; closed sprints stay browsable as history. Creating and managing sprints happens right in the sprint switcher, and the new-sprint form arrives prefilled with the next name and sensible dates.
- Switch a project between kanban and scrum whenever you like, from the project settings, without losing tasks, columns, comments or history.
- Epics. Group tasks under a named, colored epic - it shows on board cards, backlog rows and hover cards, filters every task view, and project settings track how far each epic has progressed.
- Checklists on tasks. Break a task into checkable items with inline add, rename and drag-to-reorder; board cards show a done/total counter.
- Task links and dependencies. Link tasks as *blocks*, *duplicates* or *relates to*, each side showing its own perspective, and board cards flag a task as Blocked while something open still blocks it.
- Effort estimates, in the unit each project picks in its settings - story points or hours. Estimates show on cards, rows and the task panel, and each board column totals its own. Off by default until an admin picks a unit.
- File attachments on tasks. Upload a file or pull one in from your workspace, preview and download it - everyone who can open the task can see it. Attachments now belong to the task itself, so they no longer land in the uploader's private folder where nobody else could reach them.
- Watch a task to follow it, or mute one to stop hearing about it. Watchers also get notified when a task moves or is completed, which used to notify nobody. Commenting on a task or being assigned to it starts watching it automatically - switchable in your project preferences.
- Choose how loud projects are: in-app and push, in-app only, or nothing at all - set once for all projects, and overridable per project from the bell in its header.
- Being assigned a task now notifies you, and shows up in the project activity feed. Due-date reminders no longer repeat every day: you get one when a task falls due and one when it becomes overdue, in the morning of your own timezone rather than at a fixed server hour.
- Filtering, sorting and paging now happen on the server, so a filtered board, backlog or task list is fast, shareable by URL and survives a refresh.
- The filter bar is a single compact row - search, a Filters button with a count badge, and Clear - with all the pickers in a panel underneath. It no longer breaks into ragged lines when space runs short, stays open while you use it, and shows the assignees and labels you already picked as removable chips.
- Priority and status dropdowns are styled like the assignee and label pickers, with the priority badge's colors and the status color dot.
- New projects' default columns come with colors out of the box, and existing projects were backfilled - except columns you had renamed or colored yourself.
- The task panel is reordered around what a task is: status, priority and due date first, then assignees and labels, then description and checklist, then links, attachments and the discussion. Every section folds from its header, and empty ones start folded.

### Files

- Edit office documents in the browser. Word, Excel and PowerPoint files - and their OpenDocument equivalents - now open and save directly in the file viewer when your instance is set up with an editor. Without one, office files keep their download-only behavior.
- Search inside your documents. A word that appears only in a PDF, a Word, Excel or PowerPoint file finds it now, and so do OpenDocument files, the older .doc, .xls and .ppt formats, rich text and ebooks. Documents already in your workspace are picked up too. A scan with no text in it keeps matching on its name, as it did before.

### AI Assistants

- The assistant researches instead of glancing. It now chains several lookups to answer one question, building each on the last, instead of reading one page and concluding from it.
- It reads a page for your question rather than from the top: on a long spec, manual or thread it returns what the page says on the subject, grouped by section, with the page outline so it knows where to look next - and it can now continue past the point where a long page was cut instead of re-reading the beginning.
- PDFs and news feeds are readable too. A PDF comes back as text (and says so plainly when it is a scan with nothing to read), and a site's feed comes back as a dated list of what it published lately.
- Web search gained the filters it was missing: a time range, a category, how many results, a single-site scope, and several queries in one go. "What happened this week" now actually searches this week.
- Replies come back faster: lookups that cannot affect each other now run at the same time, so a turn that reads five pages costs the slowest one rather than all five added up. Generating several images at once got the same treatment.
- Follow-up questions no longer lose the thread. What a tool found used to be cut to a fragment before the next turn saw it; the most recent turn now comes back whole.
- The assistant can work on your projects: ask what is on your plate today, find tasks across boards, then create, move, reassign, reschedule or comment on them - always within what you yourself are allowed to see.
- Bots can speak. An assistant can now reply with a voice message - the same bubble and player a recorded one gets - and each bot has a recognizable voice of its own. Needs speech synthesis configured on the instance.
- An agent goal whose deadline has passed is now flagged as overdue and closed instead of checking in forever, and an assistant can see the goals it recently finished, so it stops re-opening them or talking about them as if they were still running.

### Chat

- The "AI is typing" bubble no longer stays stuck on screen after you leave the app and come back once the reply has landed.

### Mail

- Merge duplicate folders: when your provider and your mail client each created their own Trash, Sent or Junk, tell the app they are the same folder. The one you keep shows all the mail, carries all the counts, and receives everything the app files from then on. Undo it any time from the account menu.

### Sign-in

- Groups from your identity provider can be mirrored into the workspace at login, so a team arrives with the right access to shared folders, conversations and projects on its first sign-in. Off by default, with an optional allowlist of which groups to mirror.
- An account linked to single sign-on no longer keeps its old local password alive: it is disabled at linking, and accounts linked before this release were cleaned up too. To keep external clients working - and to finally give SSO accounts a way to mount their files - WebDAV now accepts a personal API token in place of the password.

### Interface

- Preferences and settings stop looking alike: every module's display preferences now open from a sliders icon labelled "Preferences", while the gear stays for real settings.
- Confirmation dialogs are consistent across the app, each with an icon matching what it does. Deleting several files at once now asks the same clear question as deleting one - it used to fall back to a generic blue "OK" dialog.
- Hovering someone's name, and not just their picture, opens their user card - everywhere, including a project's member list, where it did nothing at all.
- The user menu now links to the project's source code and to the bug report form, with the version you are running already filled in.

### API

- The calendar endpoints are flatter and match the rest of the API: `/api/v1/calendars`, `/api/v1/events`, `/api/v1/external-calendars` and `/api/v1/polls`. The chat gallery endpoint is now `/media`. The old paths are gone - scripts calling them need updating.
- The API reference is organized by module and sub-module throughout, so large modules are no longer one undifferentiated block of fifty operations.

### Fixes

- Emptying the trash now actually removes purged folders from disk. They used to be left behind and get resurrected on the next scan, group folders included.
- Typing `[[` in a note right as it opens no longer silently fails to bring up the link picker.
- The file browser toolbar no longer wraps onto two lines on a 1280px-wide screen.
- The Storage analysis dialog's close button sits in its corner again on mobile.

## 0.37.0 - Imports & Storage

### Highlights

Moving in just got easy: connect your old cloud and watch your files arrive, with live progress and a retry for anything that fails. Once they're in, the new storage analysis shows exactly where your space goes - largest files, duplicates and all - and uploads finally ask what to do when a file with the same name already exists.

### Imports

- Bring your files over from another cloud. The new Imports page connects to your Nextcloud or any WebDAV server, lets you pick what to import and where it should land, and shows the transfer progressing live. You can leave the page and come back at any point, stop a running import, and retry just the files that failed - a notification tells you when everything is done.

### Files

- See what takes up space. The new Storage view - from the sidebar, or "Analyze storage" on any folder - breaks usage down by file type and by sub-folder (each one drillable), lists your largest files, and groups duplicate files ranked by how much space they waste. At the top of your tree it also shows your quota and how much the trash holds, with an "Empty trash" shortcut right there.
- Duplicates are detected by their actual content, not just their name - two copies of the same file count as duplicates even after one was renamed.
- You decide what happens to a name clash. Uploading, copying or moving a file into a folder that already has one with the same name now asks: Replace, Keep both, or Skip. A new file preference lets you make that choice once for good, and the end-of-upload summary tells you what happened ("Uploaded 3 files, replaced 1, skipped 1"). Previously this failed with an unhelpful "Unknown error".
- The folder picker can now browse group folders, so you can save and move things directly into a shared space.

### Administration

- The admin area is now a real operations console: it opens on a system health dashboard showing what needs attention - sync errors, failed background work - with direct links to the affected items, common recovery gestures (resync an account, retry failed thumbnails) are one-click actions, and every list gained proper search and filters. All in a fresh new look.

### Fixes

- Collapsing or expanding the sidebar updates its icon immediately, and Files, Notes and Chat no longer flicker at page load.
- Icons that reflect a changing state now redraw the moment the state changes.
- The module tiles on the dashboard are properly centered.

## 0.36.0 - Threads, SSO & Notifications

### Highlights

Conversations stay readable: side discussions now live in threads, and you decide how loud each conversation gets. Sign in with your company account through OpenID Connect, get notified when important mail lands, and see how each project is progressing on its new Analytics page.

### Chat

- Threads. Replying to a message opens the discussion in a side panel instead of pushing it into the main conversation. The original message shows how many replies it has, and only the people who took part in a thread see it as unread - a long side discussion no longer marks the whole conversation unread for everyone. Prefer the old behavior? A preference puts replies back in the main flow.
- Per-conversation notification level. From the bell in the conversation header, choose to be notified for every message, only when you are mentioned, or never. A muted conversation still shows its new messages when you open it - it just stops interrupting you.
- Assistant replies now notify you like any other message, including as push notifications on your phone.
- Chat tells you when something fails: a denied microphone or camera permission, a call that is already full, a blocked pop-up, or a voice message that could not be sent now show a clear message.
- Opening one attachment right after another always shows the one you clicked last.

### AI Assistants

- Give an agent goal a full mission brief. Beyond the objective, you can now spell out what "done" looks like, the constraints to respect while working, and when the assistant should report back. Everything is editable in one dialog - objective, brief, schedule and the assistant's own working notes - so you can steer a goal that drifts without stopping it and starting over.

### Sign-in

- Single sign-on with OpenID Connect. Connect your instance to your identity provider (Keycloak, Authentik, Authelia, Google Workspace, Microsoft Entra ID...) and sign in with your company account. The account is created on first login, keeps its display name in sync with the provider, and password changes happen there rather than in the app. Username and password sign-in keeps working alongside.

### Mail

- Get notified when mail arrives. Choose in your mail preferences to be notified for every incoming message, only when the AI classifier applies a label you flagged as important (Urgent, by default), or never. Opening the message clears the notification.

### Notifications

- Dashboard badges you can actually clear. The number on each tile now counts your unread notifications for that module and goes down as you deal with them - open the task, read the email, look at today's events - instead of staying stuck all day. Badges also update live while the dashboard is open.
- Morning reminders. Each day you receive one notification listing your due and overdue tasks and one listing today's events. Dismiss them once you have seen them; they come back the next morning if a task is still due.
- Notifications from the same module no longer overwrite each other on screen: a second chat notification used to silently replace the first before you had a chance to see it.

### Projects

- Analytics page. Every project has a new Analytics view in its sidebar to follow how the work is going: a week-by-week chart of tasks created versus tasks completed over the last three months, headline numbers on throughput, and a breakdown of open work by column, by assignee and by priority. It is built from the history your projects already keep, so it works right away on existing projects.

### Notes

- Create a note on your phone with a floating "new note" button, like in mail and calendar.

### Interface

- Relative times read the same everywhere: `just now`, `5m ago`, `2h ago`, `3d ago`, then the date after a week - the conversation list, the notifications menu and comments used to each show a different wording for the same moment.
- Pop-up notifications inside the app now match the look of the rest of the interface.
- The menus in the conversation pane have a little more breathing room between rows.

## 0.35.0 - Voice, Agents & Tags

### Highlights

Chat gains a voice: record a message with one tap and play it right in the conversation. Assistants grow up too - they can now pursue long-running goals on their own, tell you live what they are working on, and search across your whole workspace instead of one module at a time. And web push notifications, which never actually reached a single device, finally do.

### Chat & Assistant

- Record and send voice messages: a microphone button in the composer captures your message, and audio plays inline in the bubble with its own player, on desktop and mobile alike.
- Bots can pursue autonomous goals. Give an assistant a mission spanning days or months - watch a topic, follow up on something, prepare a recurring digest - and it wakes up on its own to work on it, keeps its own notes, decides when to check in next, and stays quiet unless it has something genuinely worth telling you. Goals are created in conversation or from the Start AI Chat dialog, and managed from the chat info panel.
- The typing indicator now says what the assistant is doing while it works - searching the web, reading your calendar, generating an image - instead of an anonymous animated bubble for up to half a minute. In group conversations everyone sees it, not just whoever asked.
- Cancelling a bot response actually stops it. Until now the assistant kept working to the end behind the scenes, saving memories and generating images for an answer nobody would read.
- Reloading the page in the middle of a response no longer loses the thread: the conversation picks the response back up where it was.
- The assistant can search everything at once - files, mail, events, messages, tasks - and find a colleague from a partial name, so asking about a topic no longer takes it three guesses. It can also read back a conversation and summarize it when you ask what was decided.
- Image generation is retried when the service hiccups, instead of quietly returning fewer images than you asked for and never mentioning it.
- Assistant replies no longer leak the model's raw internal reasoning, nor stray `[image: ...]` markers, into the text you read.

### Files & Tags

- Tags are now part of the file browser: each file and folder shows its tags in both list and mosaic view, a context-menu entry assigns them, and a toolbar filter narrows the listing to a single tag.
- Tags and project labels look and behave the same everywhere, with a real color palette - two colors that used to render grey now show their actual color, and tag colors are consistent between notes, files and projects.
- Filtering in mosaic view works at last: search, type and favorites filters used to apply to the list view only.

### Comments & Mentions

- Mention people with `@` in file comments and task comments, exactly as in chat: an autocomplete suggests who can see the thread, the mention renders as a badge with the profile card, and the person is notified. Only the file's or project's actual audience can be mentioned.
- Mentions now work for usernames containing dots or hyphens - the `firstname.lastname` shape never produced a badge or a notification before.

### Mail

- Replies sent from the app now thread properly: recipients' clients group them under the original message, and conversations started from the app group in your own mailbox too.
- The copy of a sent message kept in Sent now records its Bcc recipients, so you can check afterwards who was blind-copied.

### Calendar

- Task deadlines appear on the calendar. Due dates from your projects show up alongside events as light all-day annotations, with a preview on hover and a click through to the task; a sidebar switch turns the overlay off.
- Multi-day all-day events cover every day they span - a two-day event used to be painted one day short.

### Dashboard

- A tile with a single pending item opens it directly: the unread conversation, the unread email, today's event, the overdue task. With several items pending it still takes you to the module.

### Projects

- A Help dialog, reachable from the sidebar or with the `?` key, covering views, task management, task references, members, settings and API access - like the other modules already had.

### Notifications

- Web push works. Notifications had never been delivered to a single device: they failed at signing time, before any request left the server. No configuration change is needed on existing instances.
- Push delivery is also more consistent: a mention is never swallowed by grouping, a push arriving while you are in the app is retried shortly after instead of dropped, a device unsubscribed server-side re-registers itself on the next load, and a transient failure no longer blocks the following notification.
- Notifications about the same thing group together instead of stacking, and clear on their own once you open what they were about. Read notifications are removed after 90 days; unread ones are kept.

### Fixes

- Going back on mobile no longer shows stale data: mail, files, calendar, notes and projects refresh when the page is restored from the browser's history.
- Chat notifications no longer repeat the author when the conversation is named after them - `Jarvis in Jarvis` is now just `Jarvis`.
- Dragging a folder onto the sidebar to pin it works again in Chrome, and reordering pinned folders shows the drag feedback it lost.
- The mobile chat composer is back to its normal proportions: the microphone and send button take turns instead of crowding the text field, and the bar no longer jumps while recording.

## 0.34.0 - Time Zones & AI Transparency

### Highlights

The whole app now runs on your time zone: pick it once in your settings and every date follows, from mail timestamps to recurring meetings that survive daylight saving changes. Meanwhile the assistant stops being a black box: each reply can show how it reasoned and which tools it used, and it finally sees the images it creates.

### Time Zones

- Choose your time zone in Settings > Appearance and every date and time in the app follows it: mail, chat, calendar, notes, dashboard, activity feeds. It is detected automatically on your first sign-in, and if your browser later reports a different zone (say, while traveling), a banner offers to update it instead of changing it behind your back.
- Day boundaries are now yours too: "today" sections, date separators, and the daily journal note follow your zone instead of the server's clock, so a note written at 11 pm no longer lands on tomorrow's page.
- Calendar events remember the time zone they were created in. A weekly 10:00 meeting stays at 10:00 through daylight saving changes, all-day events stay on their labeled day for every participant, and calendar import and export carry the zone information correctly.

### Chat & Assistant

- See how the assistant thinks: bot replies now carry a collapsible timeline showing the reasoning steps and each tool used along the way, with the full details one click away. Failed tool calls are shown too, so you can tell why the AI missed instead of guessing.
- Assistants that support vision now actually see the images they generate or edit: they can look at the result, refine it over several steps, and remember earlier images from the conversation.
- Conversations can now be linked to user groups. Everyone in the group joins the conversation automatically, and membership follows as people join or leave the group.
- Messages with several photos or videos display as a compact mosaic instead of a tall stack, and the attachment viewer browses all the conversation's media in order with previous/next controls.
- Regenerating a conversation title now shows a spinner and updates the title everywhere as soon as it is ready. Renames made by another member appear live as well.

### Photos & Media

- The image and video viewer, in files and in chat alike, gets a proper lightbox feel: navigation arrows overlaid on the edges of the picture, arrow-key support, and swipe gestures on touch screens.

### Projects

- A new All tasks view lists every task of a project in one flat list, whatever its status, with a status filter, complementing the board and the backlog.
- Personal project task references are now based on your username (for example PERSPC-42) instead of an arbitrary number, so a reference tells you at a glance whose project it belongs to. Existing personal projects were renumbered accordingly.

### Fixes

- An upload over WebDAV that fails midway no longer damages the file it was replacing: the previous content stays intact until the new version has fully arrived.
- Copying a folder over WebDAV no longer leaves stray duplicates of its contents at the top of your tree, and moving a folder onto a name that already exists no longer breaks the files inside it.
- The backlog toolbar no longer overflows the screen on phones and small tablets.

### Self-hosting

- The metrics endpoint used by monitoring tools now requires credentials (set METRICS_USER and METRICS_PASSWORD). Instances that do not configure them keep the endpoint closed instead of exposed.

## 0.33.0 - Projects & Tasks

### Highlights

Meet Projects: a full task management app with kanban boards, a backlog, task references, comments, and search, now available to everyone. Your dashboard also gains a My Tasks widget so your most urgent work greets you first thing.

### Projects

The Projects app is out of preview and available to everyone. Here is what it brings:

- **Board and backlog.** Each project has a kanban board and a hand-ordered backlog. Drag tasks between columns, reorder them freely, and give each task a priority, a due date, labels, and assignees.
- **Personal and shared projects.** Every user gets a personal project for their own tasks; shared projects are opened to individual members or to whole groups at once, with admin and member roles.
- **Task references.** Every task carries a short reference like PRJ-42, shown on cards, in the backlog, and in activity history. Typing it (or just the number) into search jumps straight to the task, and its link can be shared with anyone on the project.
- **Task detail panel.** Clicking a task opens a panel alongside the board: title, description (with Markdown), status, priority, due date, assignees, and labels are all edited in place, with the task's full activity history below. The panel has its own URL and plays nice with the back button.
- **Comments.** Each task has a comment thread; the task's creator, assignees, and previous commenters are notified of new replies and taken straight to the conversation.
- **Filters and bulk actions.** Search by text or filter by assignee, label, or priority, on the board and in the backlog alike. In the backlog, several tasks can be selected and sent to the board in one move.
- **Project overview.** A per-project home with task counts, members, labels, and a recent activity feed where each entry links to its task. Project activity also feeds into the dashboard and profile activity streams.
- **Project settings.** Admins manage everything from a dedicated page: add, rename, recolor, and reorder board columns, curate labels, handle members and roles, attach or detach groups, and archive or delete the project.
- **Tidy Done columns.** Each project decides how long completed tasks remain visible on the board, from one day to forever. Older ones leave the board but are not deleted: they still count in the overview and remain reachable through search and direct links.
- **Quick pickers.** Assignees and labels are picked through search-as-you-type fields with removable chips, and admins can create a new label on the fly right from the picker.
- **Search.** Projects and tasks appear in the global search, matching words from descriptions as well as titles.
- **At a glance.** The Projects tile on the home page shows a badge with your assigned tasks that are due today or overdue.

### Dashboard

- A new My Tasks widget lists your open assigned tasks with the most urgent first: overdue tasks lead, then closest due dates, with priority breaking ties. Each row opens the task directly. You can turn the widget off in your dashboard preferences.

### Chat & Assistant

- You can now regenerate the title of an assistant conversation if the automatic one missed the point.
- Images generated by the assistant now open properly in the image viewer, appear in the conversation's media gallery, and keep their real format instead of always being labeled PNG.

### Notes

- The sidebar folder tree now goes as deep as your folders do. Folders nested more than four levels down used to be flattened together; they now browse normally.

### Fixes

- The AI Memory section of a conversation's info panel now actually lists the memories instead of showing a count next to an empty list, and opening the panel from the sidebar menu loads every section reliably.
- Avatars are now properly centered in user lists and chips across the app (member pickers, user search, event guests, task assignees), whether the person has a profile picture or initials.

## 0.32.0 - Sync & Reliability

### Highlights

Background syncing got a lot lighter. Several mailboxes now refresh side by side instead of queueing behind each other, one slow account no longer holds up the rest, and the same sync never runs twice over the same data.

### Mail

- Mailboxes now refresh in parallel. If you have several accounts, new mail shows up sooner, and an account that is slow, unreachable, or misconfigured no longer delays every other account behind it.
- An account that is still syncing is no longer synced a second time on top of itself. A long pass (a first sync, or a very large mailbox) used to be restarted from the top while the first one was still working, which slowed the whole mailbox down for no benefit.

### Files

- Files added outside the app, over WebDAV or dropped straight into your folder on the server, now show up more reliably. The background scan that picks them up used to fall behind and pile up on itself on large libraries; it now keeps up.

### Fixes

- The recent activity feed on your dashboard and profile no longer lists entries, or filter tabs, from apps you do not have access to.

## 0.31.0 - Search Everywhere

### Highlights

One search, everywhere: mail, chat, and calendar now share the same smarter search, and chat messages finally show up in the global search.

### Search

- Mail search now understands what you mean: matching is accent-insensitive ("cafe" finds "café"), works on whole words, and the most relevant messages come first instead of a raw date-ordered dump. It also stays fast on very large mailboxes. The same smarter matching applies in the mail app's search box, the global search, and when you ask the assistant to find a message.
- You can now find chat messages from the global search: results show the conversation they belong to, and direct messages are labeled with the other person's name. Searching inside a conversation and asking the assistant to search your messages use the same improved matching, with results ranked by relevance and recency.
- Calendar search now looks beyond event titles: a word from an event's description or its location is enough to find it, in the global search and when asking the assistant.

### Profile & UI

- People without a profile picture now get initials on a color of their own instead of a uniform grey circle. Each person keeps the same color everywhere - chat, mail, calendar, member lists - so they become recognizable at a glance.

## 0.30.0 - File Tags & Performance

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
