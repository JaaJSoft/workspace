# Claude Code Instructions

## Workflow

### Git

- Never commit automatically. Only commit when I explicitly ask for it.
- Do not use git worktrees. Work directly on the current branch.

### Refactoring & Optimization

Before any refactor or optimization, verify that at least one test covers the code being touched. If no test exists, **write the test first** (it must pass against the current code), then start the refactor. The test acts as a safety net to guarantee the behavior is preserved.

### Changelog

`CHANGELOG.md` is written for **end users**, not developers. Each release describes what changed from the user's perspective, in plain language.

**Structure of a release entry:**

1. `## <version> — <title>` heading. The title is a short thematic label (2–4 words) summarizing the release theme: *Performance & Reliability*, *Calendar Overhaul*, *Profile & Rich Media*. It shows up next to the version number in the in-app "What's new" modal. Em-dash (`—`), en-dash (`–`), hyphen (`-`), and colon (`:`) are all accepted as separators; the title is optional but recommended for non-patch releases.
2. `### Highlights` — one short paragraph (2–4 sentences) summarizing the theme of the release and what users will notice. No bullet list here.
3. Then one `###` section per user-facing area (module name or feature theme: *Chat*, *Files & Notes*, *Calendar*, *WebDAV*, *Profile & UI*, *Performance*, *API Tokens*, *Fixes*, …).

**What to include:** new features, visible improvements, behavior changes, user-visible bug fixes, performance gains phrased as *"faster X"* / *"quicker Y"*, new integrations or endpoints that users can call.

**What to exclude (do not write these in the changelog):**
- Refactors with no visible effect (`services.py` → `services/` package, extracting helpers, centralizing logic, moving code between modules)
- Internal test additions, coverage thresholds, CI changes
- Documentation-only changes (including CLAUDE.md updates)
- Dependency bumps, unless they bring a user-visible feature or fix
- Implementation details: library names (Knox, alpine-ajax, Celery…), query patterns (N+1, `bulk_update`, composite indexes, prefetch), internal APIs (`FileService.X`, `ActionRegistry`, `$ajax`), framework-specific terms (`transaction.atomic`, `x-target`, serializer fields)

**Tone:** describe the outcome, not the mechanism. ✅ *"Faster conversation listings"* ❌ *"Added composite index on `conversation_member(user_id, left_at)`"*. ✅ *"Large uploads are more reliable on slow networks"* ❌ *"Streamed WebDAV PUT for TCP backpressure"*.

**Process:** when preparing a release, read commits since the last tag (`git log v<last>..HEAD --oneline`), group them by user-facing theme, then translate each group into one bullet the user can understand. Commits that map to nothing user-visible are dropped — not every commit deserves an entry.

## Testing

### Structure

Every module must have its tests inside a `tests/` package (directory with `__init__.py`), **not** a single `tests.py` file. Test files must follow the `test_*.py` naming convention (never `tests_*.py`).

```
workspace/<module>/tests/
├── __init__.py
├── test_models.py
├── test_views.py
└── ...
```

### CI

Tests run in parallel in CI with one job per module (see `.github/workflows/tests.yml`). When creating a new Django app module, add it to the `matrix.module` list in the workflow file.

## Backend Conventions

### API

All API endpoints must be prefixed with `/api/` and have no trailing slashes.

### Models

Every model must use a UUID primary key with the `uuid_v7_or_v4` helper as default. Never use Django's auto-incremented `id` or `uuid.uuid4` directly.

```python
from workspace.common.uuids import uuid_v7_or_v4

class MyModel(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
```

### Services

Business logic that doesn't belong in views, models, or tasks lives in **services**. Services are reusable across views, REST endpoints, Celery tasks, and management commands.

#### Layout

Every module exposes its services through a `services/` **package** (directory with `__init__.py`), never a single `services.py` file:

```
workspace/<module>/
├── services/
│   ├── __init__.py    # empty — DO NOT re-export
│   ├── <name1>.py
│   └── <name2>.py
├── tests/
│   ├── test_<name1>.py
│   └── test_<name2>.py
└── ...
```

Examples in the codebase: `files/services/{files,mime,thumbnails}.py`, `chat/services/{conversations,notifications,rendering,avatar,typing,link_preview}.py`, `mail/services/{imap,smtp,oauth2}.py`.

#### Naming rules

- File names describe **what the file contains** (a feature, an entity, an integration) — they **never contain the word "service"**. ✅ `chat/services/conversations.py` ❌ `chat/services/conversation_service.py`
- One distinct concern per file. If a single file mixes 3+ unrelated topics (membership / notifications / rendering), split it.
- Tests follow the same naming: `tests/test_<name>.py` — never `tests/test_<name>_service.py`.

#### Imports

- Default: import from the explicit submodule — `from workspace.<module>.services.<name> import X`. Keep `__init__.py` empty.
- Re-exports in `__init__.py` are allowed **only** for a canonical class/value that defines the module's core entity (e.g., `FileService` in `files/services/__init__.py`). Never re-export functions you patch in tests — `@patch('workspace.X.services.fn')` would patch the alias in `__init__`, not the call site, and silently do nothing.
- Relative imports inside a service file must escape the `services/` package with `..`:
  ```python
  # In workspace/chat/services/conversations.py
  from ..models import Conversation, ConversationMember   # ✅
  from .models import Conversation                        # ❌ resolves to services/models — doesn't exist
  ```
- For unavoidable package-style imports (`from workspace.X import old_name_service`), alias to keep call sites unchanged:
  ```python
  from workspace.users.services import settings as settings_service
  ```
  Use this only when many call sites read `settings_service.X` and renaming all of them is out of scope.

#### Test patches

`@patch('workspace.<module>.services.<name>.symbol')` patches the symbol at its **definition site**. Patch there, not at a re-export alias — patches at an alias site bind a different name and the actual call site keeps running unmocked.

### Access Control Querysets

Never duplicate access/permission querysets. Always use the centralized helpers listed below. Each module exposes its access control logic through its `services/` package or a `queries.py` module. This ensures permission logic is defined once per module and stays consistent across views, API endpoints, and background tasks.

**Rules:**
- Never write raw ORM filters to check access rights (e.g. `File.objects.filter(owner=user)`) — always call the corresponding helper.
- When adding a new view or API endpoint, import and use the existing helper rather than reimplementing the logic.
- If a module doesn't have a helper yet, create one in its `services/` package or `queries.py` and use it everywhere.

#### Chat — `workspace.chat.services.conversations`

```python
from workspace.chat.services.conversations import user_conversation_ids, get_active_membership

conv_ids = user_conversation_ids(user)  # returns queryset of conversation UUIDs

# Single-conversation access check — returns ConversationMember or None:
membership = get_active_membership(user, conversation_id)
```

- `user_conversation_ids`: returns conversation UUIDs where the user is an active member (`left_at__isnull=True`).
- `get_active_membership`: returns the active `ConversationMember` for a specific conversation, or `None`. Use this for per-view access checks.

#### Mail — `workspace.mail.queries`

```python
from workspace.mail.queries import user_account_ids
account_ids = user_account_ids(user)  # returns queryset of account UUIDs
```

Returns mail account UUIDs owned by the user. Use for filtering messages: `MailMessage.objects.filter(account_id__in=account_ids, ...)`.

#### Calendar — `workspace.calendar.queries`

```python
from workspace.calendar.queries import visible_calendar_ids, visible_calendars, visible_events_q

# For calendar-level queries — all visible IDs (owned incl. external + subscribed):
cal_ids = visible_calendar_ids(user)

# For UI display — split owned (excl. external) / subscribed querysets:
owned, subscribed = visible_calendars(user)

# For event-level queries (owned calendars + subscribed calendars + event membership):
events = Event.objects.filter(visible_events_q(user), title__icontains=query)
```

#### Files — `workspace.files.services.FileService`

```python
from workspace.files.services import FileService

# All accessible files (owned + group + shared) — returns Q filter, does NOT filter deleted_at:
q = FileService.accessible_files_q(user)

# Personal files only (owned, non-deleted, no group):
qs = FileService.user_files_qs(user)

# Group files only (non-deleted, from user's groups):
qs = FileService.user_group_files_qs(user)

# Single-file permission check — returns FilePermission (MANAGE/EDIT/WRITE/VIEW) or None:
perm = FileService.get_permission(user, file_obj)

# Quick boolean access check:
if FileService.can_access(user, file_obj):
    ...
```

### User Settings — always go through `workspace.users.services.settings`

Per-user preferences live in the `UserSetting(user, module, key, value)` model and are wrapped by service helpers that maintain a **5-minute cache** on reads and **invalidate that cache on every write**. Never touch `UserSetting.objects` directly from views, serializers, tasks, or other services — the cache will go stale and subsequent reads will silently return the previous value until the TTL expires or the process restarts.

```python
from workspace.users.services.settings import (
    get_setting, set_setting, delete_setting, get_module_settings,
)

# Read with default (cached, 5-min TTL):
show = get_setting(user, 'dashboard', 'show_upcoming_events', default=True)

# Write (updates DB AND invalidates cache):
set_setting(user, 'dashboard', 'show_upcoming_events', False)

# Delete (DB row removed AND cache invalidated):
delete_setting(user, 'dashboard', 'show_upcoming_events')

# Read all keys for a module at once (cached 5 min, invalidated on any set/delete in that module):
prefs = get_module_settings(user, 'dashboard')
```

**Rules:**
- Never call `UserSetting.objects.create/update/delete/update_or_create` from application code — use `set_setting`/`delete_setting` instead. Raw ORM bypasses the cache invalidation and causes "F5 reverts my setting" bugs.
- The REST endpoint `PUT/DELETE /api/v1/settings/<module>/<key>` already delegates to these helpers — new UI that toggles a setting should just call it (fire-and-forget `fetch` is the idiom, see `themePickerForm()` and `dashboardPrefsForm()` in `settings_preferences.html`).
- In tests that call `set_setting`/`delete_setting`, **always `cache.clear()` in `tearDown`**. Django's `LocMemCache` is process-global and is NOT reset between `TestCase` runs, so leaked cache entries can cause order-dependent failures.

```python
from django.core.cache import cache

class MyTests(TestCase):
    def tearDown(self):
        cache.clear()
```

### Logging — sanitize user-controlled values with `scrub()`

Any value taken from request data, request headers, URL path/query, filenames, DB rows that originated in user input (a stored push-subscription endpoint, a free-text title, an email/domain, etc.), or third-party API responses **must** pass through `scrub()` before reaching a `logger.X(...)` call. This prevents log injection (CWE-117): without it, `\r\n` in user input forges fake log lines and breaks SIEM parsers.

```python
from workspace.common.logging import scrub

logger.info("Autodiscover failed for domain %s", scrub(domain))
logger.warning("Push failed for %s: %s", scrub(sub.endpoint[:60]), e)
logger.exception("Activity provider '%s' failed", scrub(source))
```

**Rules:**

- Sanitize at the logger call site even when the value looks "safe" (a validated UUID, an enum slug, an email that passed `EmailField`). Validation runs at the view boundary, but the same value flows through Celery tasks, signals, and services to loggers far from where it was checked. CodeQL traces taint, not validation — the `py/log-injection` alert fires regardless.
- Never log full request bodies or headers. If you must, scrub them.
- Internal/system values that never touched user input (settings keys, hard-coded enum members, `__name__`, computed counts) don't need `scrub()`. Apply it to the *tainted* fields, not the whole format string.
- The helper lives in `workspace/common/logging_safe.py`. The `str(...).replace('\r','').replace('\n','')` chain inside is the exact form CodeQL recognizes as a sanitizer for `py/log-injection` — do not refactor the replaces away or wrap them in another helper.

## Frontend Conventions

### Alpine `init()` is auto-called — never add `x-init="init()"` on top of it

If your `x-data` component defines an `init()` method (`x-data="myApp()"` where `myApp` returns an object with `init() { ... }`), Alpine **automatically** invokes it when the element mounts. Adding `x-init="init()"` next to `x-data="myApp()"` runs `init()` a **second time**, silently:

```html
<!-- ❌ WRONG — init() runs twice -->
<div x-data="chatApp()" x-init="init()"></div>

<!-- ✅ Correct — Alpine auto-calls init() once -->
<div x-data="chatApp()"></div>
```

The bug is invisible: the second pass overwrites the first with the same data, no console warning, no broken UI. The visible cost is **double API calls and double event-listener registration** for everything in `init()`. We hit this in 4 modules (chat, mail, notes, dashboard) and the only diagnostic was a network-level audit.

**Rules:**
- Component objects with an `init()` method must rely on Alpine's auto-call. Do **not** also write `x-init="init()"`.
- `x-init` is only for **inline expressions** on components that don't define an `init()` method (e.g., `<div x-data="{ open: false }" x-init="$watch('open', ...)">`).
- When adding event listeners inside `init()`, remember they will be added once per mount — if you ever do see two listeners firing, suspect a duplicate `x-init` or a duplicate `x-data` instantiation of the same component (see `filePreferences()` in `files/ui/index.html`, instantiated twice intentionally — its `init()` should be guarded against re-fetching).

### Embedding view data into JS — use `|json_script`, never `orjson.dumps + |safe`

When a Django view needs to hand off data to client-side JS (initial state, server-rendered preferences, serialized querysets that would otherwise force a redundant API call), **pass the raw Python object in context** and render it with Django's built-in `|json_script` filter:

```python
# View — pass the raw dict/list (NOT a JSON string)
return render(request, 'mail/ui/index.html', {
    'accounts': MailAccountSerializer(accounts, many=True).data,
    'oauth_providers': get_available_providers(),
})
```

```django
{# Template — |json_script renders <script id="..." type="application/json">...</script> #}
{{ accounts|json_script:"accounts-data" }}
{{ oauth_providers|json_script:"oauth-providers-data" }}
```

```js
// JS — read from the DOM
const accounts = JSON.parse(document.getElementById('accounts-data').textContent);
const providers = JSON.parse(document.getElementById('oauth-providers-data').textContent);
```

**Never** do this:

```python
# ❌ Manual dump in the view
'accounts_json': orjson.dumps(serializer.data).decode(),
```
```django
{# ❌ Inline raw JSON via |safe — XSS surface, no auto-escaping of </script> #}
<script id="accounts-data" type="application/json">{{ accounts_json|safe }}</script>
```

**Why `|json_script` is mandatory here:**
- It escapes `<`, `>`, `&`, `'`, `\u2028`, `\u2029` as JS-safe Unicode escapes — `</script>` injection is impossible even if the data contains user-controlled strings. Manual `|safe` defeats Django's auto-escape entirely; you'd have to remember to do `.replace('</', '<\\/')` everywhere (and inevitably forget once).
- It produces a `<script type="application/json">` block, which the browser parses as data, not code — no eval, no parser tricks.
- It's built into Django (since 2.1) — no extra import, no `orjson`/`json` boilerplate in the view.

**Naming convention — drop `_json` from context variable names:** the value passed to the template is now a Python dict/list, not a JSON string. Naming it `accounts_json` is a lie. Always name the context variable for what it *is*:

| ❌ Old name | ✅ New name | Type at the view boundary |
|---|---|---|
| `accounts_json` | `accounts` | dict / list |
| `prefs_json` | `prefs` | dict |
| `calendars_json` | `calendars` | dict |
| `folders_json` | `folders` | list |

The script tag's `id` attribute is the right place for the `*-data` suffix (e.g., `id="accounts-data"`), not the Python context key.

**Exception** — when a context variable name collides with another already in context (e.g., a view passes both a queryset of accounts and the serialized version), check whether the queryset version is actually used in the template. It is often **dead context** (the template only reads `accounts` from the JS side via the embedded script tag). If so, delete the dead key; don't keep both.

### Server-rendered partial swaps — use alpine-ajax, never raw `fetch`

Whenever a piece of UI needs to be refreshed from a Django partial (lists, feeds, sidebars, popovers, folder trees, anything rendered server-side), **use [alpine-ajax](https://alpine-ajax.js.org)**. The library is already loaded globally in `base.html`.

**Never** write a hand-rolled `fetch(...).then(r.text()).then(html => el.innerHTML = html)`, `DOMParser` parsing of an HTML response, or `target.replaceWith(newNode)` + `Alpine.initTree()` pipeline. That pattern silently destroys every Alpine binding inside the swapped subtree (context menus, drag-and-drop handlers, `x-show` state, `x-model` bindings) because raw `innerHTML` assignment doesn't morph — it rebuilds the tree from scratch.

#### How to trigger a swap

**Declarative (user-triggered):** put `x-target="<target-id>"` on the `<a>` / `<form>` / `<button>` the user interacts with. The response must contain an element with matching `id`.

```html
<a href="{% url 'chat_ui:conversation_list' %}" x-target="conversation-list">Refresh</a>

<div id="conversation-list">
  {% include "chat/ui/partials/conversation_list.html" %}
</div>
```

**Programmatic (from an Alpine expression or a component method):** use the `$ajax(url, options)` magic. This is the **only** supported way to initiate a request from JavaScript — do not fake a user click on a hidden link.

```html
<!-- Inline Alpine expression -->
<input @input.debounce.300ms="$ajax('/chat/conversations?q=' + encodeURIComponent(query), { target: 'conversation-list' })">
```

```js
// Inside a component method (x-data): `this.$ajax` is available just like `this.$refs`.
refreshList() {
  this.$ajax('/chat/conversations', { target: 'conversation-list' });
}
```

Available `$ajax` options: `method` (default `'GET'`), `target` (id of the element to swap, **without** a `#`), `targets` (array, overrides `target`), `body`, `headers`, `focus`, `sync`.

#### Lifecycle events

`ajax:before`, `ajax:send`, `ajax:success`, `ajax:error`, `ajax:after` bubble up the DOM. Listen on the component root with `@ajax:error="showAlert('error', 'Failed')"` instead of wrapping the call in a `try/catch`.

#### Server side

Return the partial template directly — no JSON envelope, no wrapping. The endpoint often checks `request.headers.get('X-Alpine-Request')` so the same URL can serve the full page (browser refresh) and the fragment (alpine-ajax swap). Existing examples: `workspace/chat/ui/views.py:conversation_list_view`, `workspace/users/ui/views.py:profile_activity_feed`, `workspace/files/ui/views.py` (the `#folder-browser` branch).

### UI Partials

Always use the existing UI partials located in `workspace/common/templates/ui/partials/` instead of writing inline HTML for common components.

#### Alerts

Use the `inline_alert` partial for all alert messages:

```django
{% include "ui/partials/inline_alert.html" with type="error" message="Your error message" %}
{% include "ui/partials/inline_alert.html" with type="warning" message="Your warning message" %}
{% include "ui/partials/inline_alert.html" with type="success" message="Your success message" %}
{% include "ui/partials/inline_alert.html" with type="info" message="Your info message" %}
```

Available parameters:
- `type`: 'info' (default), 'success', 'warning', 'error'
- `message`: The message to display
- `title`: Optional title
- `dismissible`: True/False - adds close button
- `icon`: True (default) / False - show/hide icon
- `class`: Additional CSS classes (e.g., "mb-4")

#### Dialogs

Use the `dialogs` partial for modal dialogs instead of inline modal HTML.

#### Other Available Partials

- `app_logo.html` - Application logo
- `breadcrumbs.html` - Breadcrumb navigation
- `navbar.html` - Navigation bar
- `refresh_button.html` - Alpine-AJAX refresh button (spins while `loading` is truthy). Params: `url_expr`, `target`, optional `loading_expr` / `title` / `size`.
- `user_avatar.html` - User avatar display

### File Actions

Any frontend element that triggers an action on a file or folder (rename, delete, favorite, share, move, pin, download, etc.) **must** check availability against `POST /api/v1/files/actions` before letting the user click. Never hard-code availability rules in the frontend (no "is journal note?", no "is owner?", no "is shared with me?" checks duplicated client-side). The backend `ActionRegistry` (`workspace/files/actions/`) is the single source of truth, and `RenameAction.is_available()` / `DeleteAction.is_available()` / etc. already encode all the rules.

**Rules:**

- Context menus: fetch the action list for the target file(s) via `/api/v1/files/actions` and render only the returned actions — never render a static list of menu items.
- Buttons, links, inline inputs (e.g., title input for rename): bind their `:disabled` / `:readonly` attribute to the presence of the corresponding action ID in the fetched list. Default to disabled while the list is loading (fail-safe).
- When implementing a new file-manipulating UI element, first check that an `is_available` entry exists for the action in `workspace/files/actions/`. If not, add it — don't ship the UI without it.
- Defence-in-depth: the JS handler that performs the action (e.g., `renameNote`, `deleteNote`) must also early-return if the action isn't in the cached list. Prevents stale state from producing a request the backend will 403 anyway.

**Endpoint contract:**

```js
const resp = await fetch('/api/v1/files/actions', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
  body: JSON.stringify({ uuids: [fileUuid] }),
});
const data = await resp.json();
// data[fileUuid] is an array of { id, label, icon, category, shortcut, css_class, bulk }
const actionIds = (data[fileUuid] || []).map(a => a.id);
```

Use a race-protection counter (see `_loadGeneration` in `workspace/notes/ui/static/notes/ui/js/notes.js:557`) when the fetched list feeds reactive state that depends on the current selection — rapid selection changes otherwise lead to stale results being applied.

**Scope:** applies to the `files` module and every module whose UI manipulates files (notes, mail attachments, chat attachments, etc.). If a module manipulates another kind of entity with its own action registry (not files), follow the same principle against that module's equivalent endpoint.
