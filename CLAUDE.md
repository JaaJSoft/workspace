# Claude Code Instructions

## Commands

```bash
# Setup
uv sync                                        # install dependencies
uv run python manage.py migrate                # apply migrations
uv run python manage.py runserver              # dev server on :8000

# Tests (per module - matches CI matrix)
uv run python manage.py test workspace.<module>           # e.g. workspace.files
uv run coverage run manage.py test workspace.<module>     # with coverage

# Async stack
uv run celery -A workspace worker -l info
uv run celery -A workspace beat -l info

# Vendored editor bundle (rebuild after bumping @milkdown/* in scripts/editor/package.json)
cd scripts/editor && npm run build:editor
```

**CI coverage floors** (`.github/workflows/tests.yml`): each module pins a `min_coverage` (45-95%). Lowering a threshold is forbidden by the workflow's own comment - raise it after adding coverage, never lower it.

## Module Map

Each Django app under `workspace/` follows the same shape (`models.py`, `views.py`, `services/`, `tests/`, `ui/`, `urls.py`):

| Module | Purpose |
|---|---|
| `ai` | LLM tools, AI assistants, prompt routing |
| `calendar` | Events, recurrence, external calendar sync |
| `chat` | Conversations, messages, typing indicators, link previews |
| `common` | Cross-cutting helpers: UUIDs, booleans, logging, cache, mixins |
| `core` | Auth, navigation, changelog, dashboard scaffolding |
| `dashboard` | User home page widgets |
| `files` | File/folder model, permissions, WebDAV, thumbnails, sharing |
| `mail` | IMAP/SMTP, OAuth2 providers, labels, autodiscover |
| `notes` | Markdown notes built on the files module |
| `notifications` | Web push, in-app notifications |
| `users` | User model, settings, profile, activity feed |

## Infrastructure

- **Cache & sessions:** Redis (`django-redis`). Sessions are NOT in the DB in production - don't count `SELECT django_session` as a prod cost.
- **Async tasks:** Celery + Redis broker. Background work (mail sync, thumbnails, push) runs via tasks; never block a request on it.
- **Database:** PostgreSQL canonical, SQLite for dev/tests (see `core/management/commands/sqlite_to_postgres.py` for migration).

## Workflow

### Git

- Never commit automatically. Only commit when I explicitly ask for it.
- **Never commit to `master`/`main`.** These branches are protected: no direct commits, ever, even when I explicitly ask to commit. If we're on `master`/`main` and a commit is warranted, create a branch first (`type/short-subject`, e.g. `feat/theme-picker`) and commit there. Committing directly is only acceptable when I have already checked out a non-default branch.
- Committing to a feature branch is fine when we're in a structured flow (a design document, spec, or written plan exists for the work). Absent such a flow, still branch off `master`/`main` rather than committing to it.
- Do not use git worktrees. Work directly on the current branch (creating a feature branch when the current branch is `master`/`main`, per the rule above).
- Never mention "Claude", "Claude Code", "CLAUDE.md", or any AI/assistant attribution in commit messages, commit titles, PR titles, or PR descriptions. The user wants commits and PRs to read as if a human wrote them. This includes the trailing "🤖 Generated with [Claude Code]" footer and the "Co-Authored-By: Claude" trailer - omit both. References to project rules should cite the rule itself ("per the no-logic-change refactor contract"), not the file ("per CLAUDE.md").
- All commit messages **and** PR titles must follow the Conventional Commits format `type(scope): subject` (e.g. `feat(theme): split theme picker into light and dark slots`, `fix(chat): prevent duplicate retry`). Allowed types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `style`, `build`, `ci`, `revert`. Subject is lowercase, imperative mood, no trailing period. This applies to PR titles too - don't pass a free-form title to `gh pr create`, prefix it.

### Refactoring & Optimization

Before any refactor or optimization, verify that at least one test covers the code being touched. If no test exists, **write the test first** (it must pass against the current code), then start the refactor. The test acts as a safety net to guarantee the behavior is preserved.

### Bug Fixes

Every bug fix must ship with a regression test. Write the test alongside the fix and **verify it fails against the buggy code** (e.g. by stashing the fix, running the test, then re-applying), so you have evidence the test actually pins the bug down rather than accidentally passing for unrelated reasons. Without this proof the test is decorative: a future regression of the same bug would slip through CI. The test belongs in the same module's `tests/` package as the code being fixed.

**Exception - purely visual/CSS fixes don't get a unit test.** This rule targets *behavioral* bugs (backend logic, parsing, permissions, data handling). For a fix that only changes presentation (Tailwind/daisyUI classes, template markup, spacing, alignment, responsive layout) with no change in behavior, **do not** add a test that asserts CSS class strings are present in rendered HTML (`assertIn('h-auto', html)`). Such tests are worthless: they re-encode the template's class list at the same level of abstraction, they pass even when the layout is visually broken (a class string being present proves nothing about how it renders), and they break on any equally-correct restyle. Validate visual fixes by eye (or a real browser/Playwright rendering test that checks computed geometry if a genuine safety net is warranted) - never by class-presence assertions. Recompiling the CSS bundle after class changes is still required.

### Changelog

`CHANGELOG.md` is written for **end users**, not developers. Each release describes what changed from the user's perspective, in plain language.

**Structure of a release entry:**

1. `## <version> - <title>` heading. The title is a short thematic label (2-4 words) summarizing the release theme: *Performance & Reliability*, *Calendar Overhaul*, *Profile & Rich Media*. It shows up next to the version number in the in-app "What's new" modal. Em-dash (`-`), en-dash (`-`), hyphen (`-`), and colon (`:`) are all accepted as separators; the title is optional but recommended for non-patch releases.
2. `### Highlights` - 1-2 punchy sentences selling the release: what users will notice, phrased to make the update feel worth installing, without overselling. Keep it short relative to the sections below (those carry the detail). No bullet list here.
3. Then one `###` section per user-facing area (module name or feature theme: *Chat*, *Files & Notes*, *Calendar*, *WebDAV*, *Profile & UI*, *Performance*, *API Tokens*, *Fixes*, …).

**What to include:** new features, visible improvements, behavior changes, user-visible bug fixes, performance gains phrased as *"faster X"* / *"quicker Y"*, new integrations or endpoints that users can call.

**What to exclude (do not write these in the changelog):**
- Refactors with no visible effect (`services.py` → `services/` package, extracting helpers, centralizing logic, moving code between modules)
- Internal test additions, coverage thresholds, CI changes
- Documentation-only changes (including CLAUDE.md updates)
- Dependency bumps, unless they bring a user-visible feature or fix
- Implementation details: library names (Knox, alpine-ajax, Celery…), query patterns (N+1, `bulk_update`, composite indexes, prefetch), internal APIs (`FileService.X`, `ActionRegistry`, `$ajax`), framework-specific terms (`transaction.atomic`, `x-target`, serializer fields)

**Tone:** describe the outcome, not the mechanism. ✅ *"Faster conversation listings"* ❌ *"Added composite index on `conversation_member(user_id, left_at)`"*. ✅ *"Large uploads are more reliable on slow networks"* ❌ *"Streamed WebDAV PUT for TCP backpressure"*.

**Process:** when preparing a release, read commits since the last tag (`git log v<last>..HEAD --oneline`), group them by user-facing theme, then translate each group into one bullet the user can understand. Commits that map to nothing user-visible are dropped - not every commit deserves an entry.

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

### JS unit tests

Frontend helpers are tested with Node's built-in test runner - no npm dependencies, no package.json:

```bash
node --test "workspace/*/tests/js/**/*.test.js"    # Node >= 22
```

- Test files live in `workspace/<module>/tests/js/<name>.test.js`, next to the module's Python tests. No `__init__.py` in `js/`, so Django test discovery ignores it.
- Production JS files are classic scripts (globals + `window.X = ...`), not ES modules - they can't be `require()`d. Load them through the shared loader (`workspace/common/tests/js/loader.js`), which executes the file in a `node:vm` context with browser-like `window === globalThis` semantics and returns the context:

```js
const { loadScript } = require('../../../common/tests/js/loader');
const ctx = loadScript('workspace/common/static/ui/js/uuid.js');
assert.equal(ctx.isValidUuid('...'), true);
```

- Only top-level `function`/`var` declarations and `window.X` assignments are reachable on the returned context; top-level `const`/`let` are not (global lexical scope). Test the public surface.
- If a script touches `document`/`fetch` at load time, pass stubs: `loadScript(path, { document: stub })`.
- **Cross-realm gotcha:** arrays/objects created inside the vm carry that realm's prototypes, so `assert.deepStrictEqual` fails its prototype check against test-side literals ("same structure but not reference-equal"). Normalize first: `Array.from(ctx.fn(...))` or `{ ...result }`.
- CI runs these in the `js` job of `.github/workflows/tests.yml`.

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
│   ├── __init__.py    # empty - DO NOT re-export
│   ├── <name1>.py
│   └── <name2>.py
├── tests/
│   ├── test_<name1>.py
│   └── test_<name2>.py
└── ...
```

Examples in the codebase: `files/services/{files,mime,thumbnails,sharing,events}.py`, `chat/services/{conversations,notifications,rendering,avatar,typing,link_preview}.py`, `mail/services/{imap_connection,imap_folders,imap_mailbox,imap_messages,imap_parse,imap_sync,label_counts,smtp,oauth2}.py`.

#### Naming rules

- File names describe **what the file contains** (a feature, an entity, an integration) - they **never contain the word "service"**. ✅ `chat/services/conversations.py` ❌ `chat/services/conversation_service.py`
- One distinct concern per file. If a single file mixes 3+ unrelated topics (membership / notifications / rendering), split it.
- Tests follow the same naming: `tests/test_<name>.py` - never `tests/test_<name>_service.py`.

#### Imports

- Default: import from the explicit submodule - `from workspace.<module>.services.<name> import X`. Keep `__init__.py` empty.
- Re-exports in `__init__.py` are allowed **only** for a canonical class/value that defines the module's core entity (e.g., `FileService` in `files/services/__init__.py`). Never re-export functions you patch in tests - `@patch('workspace.X.services.fn')` would patch the alias in `__init__`, not the call site, and silently do nothing.
- Relative imports inside a service file must escape the `services/` package with `..`:
  ```python
  # In workspace/chat/services/conversations.py
  from ..models import Conversation, ConversationMember   # ✅
  from .models import Conversation                        # ❌ resolves to services/models - doesn't exist
  ```
- For unavoidable package-style imports (`from workspace.X import old_name_service`), alias to keep call sites unchanged:
  ```python
  from workspace.users.services import settings as settings_service
  ```
  Use this only when many call sites read `settings_service.X` and renaming all of them is out of scope.

#### Test patches

`@patch('workspace.<module>.services.<name>.symbol')` patches the symbol at its **definition site**. Patch there, not at a re-export alias - patches at an alias site bind a different name and the actual call site keeps running unmocked.

### Re-exports - ask before adding

Re-exporting a symbol (via `__all__`, a top-level `from .x import y` whose only purpose is to surface `y` from a different module, or any other indirection that lets a caller `from workspace.A import X` when `X` is actually defined in `workspace.B`) creates a "where is this defined?" maze. It also breaks `@patch` at the call site (see *Test patches* above) and makes refactors that move the definition silently leak the old path.

**Never introduce a new re-export - even to preserve a single test import, even to keep a constant reachable from where it used to live - without explicit user approval.** Default: update the call sites (including tests) to import from the definition module directly. When you genuinely think a re-export is warranted (e.g. a canonical class that defines the module's core entity), say so and ask before adding it.

### Access Control Querysets

Never duplicate access/permission querysets. Always use the centralized helpers listed below. Each module exposes its access control logic through its `services/` package or a `queries.py` module. This ensures permission logic is defined once per module and stays consistent across views, API endpoints, and background tasks.

**Rules:**
- Never write raw ORM filters to check access rights (e.g. `File.objects.filter(owner=user)`) - always call the corresponding helper.
- When adding a new view or API endpoint, import and use the existing helper rather than reimplementing the logic.
- If a module doesn't have a helper yet, create one in its `services/` package or `queries.py` and use it everywhere.

#### Chat - `workspace.chat.services.conversations`

```python
from workspace.chat.services.conversations import user_conversation_ids, get_active_membership

conv_ids = user_conversation_ids(user)  # returns queryset of conversation UUIDs

# Single-conversation access check - returns ConversationMember or None:
membership = get_active_membership(user, conversation_id)
```

- `user_conversation_ids`: returns conversation UUIDs where the user is an active member (`left_at__isnull=True`).
- `get_active_membership`: returns the active `ConversationMember` for a specific conversation, or `None`. Use this for per-view access checks.

#### Mail - `workspace.mail.queries`

```python
from workspace.mail.queries import user_account_ids
account_ids = user_account_ids(user)  # returns queryset of account UUIDs
```

Returns mail account UUIDs owned by the user. Use for filtering messages: `MailMessage.objects.filter(account_id__in=account_ids, ...)`.

#### Calendar - `workspace.calendar.queries`

```python
from workspace.calendar.queries import visible_calendar_ids, visible_calendars, visible_events_q

# For calendar-level queries - all visible IDs (owned incl. external + subscribed):
cal_ids = visible_calendar_ids(user)

# For UI display - split owned (excl. external) / subscribed querysets:
owned, subscribed = visible_calendars(user)

# For event-level queries (owned calendars + subscribed calendars + event membership):
events = Event.objects.filter(visible_events_q(user), title__icontains=query)
```

#### Files - `workspace.files.services.FileService`

```python
from workspace.files.services import FileService

# All accessible files (owned + group + shared) - returns Q filter, does NOT filter deleted_at:
q = FileService.accessible_files_q(user)

# Personal files only (owned, non-deleted, no group):
qs = FileService.user_files_qs(user)

# Group files only (non-deleted, from user's groups):
qs = FileService.user_group_files_qs(user)

# Single-file permission check - returns FilePermission (MANAGE/EDIT/WRITE/VIEW) or None:
perm = FileService.get_permission(user, file_obj)

# Quick boolean access check:
if FileService.can_access(user, file_obj):
    ...
```

### User Settings - always go through `workspace.users.services.settings`

Per-user preferences live in the `UserSetting(user, module, key, value)` model and are wrapped by service helpers that maintain a **5-minute cache** on reads and **invalidate that cache on every write**. Never touch `UserSetting.objects` directly from views, serializers, tasks, or other services - the cache will go stale and subsequent reads will silently return the previous value until the TTL expires or the process restarts.

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
- Never call `UserSetting.objects.create/update/delete/update_or_create` from application code - use `set_setting`/`delete_setting` instead. Raw ORM bypasses the cache invalidation and causes "F5 reverts my setting" bugs.
- The REST endpoint `PUT/DELETE /api/v1/settings/<module>/<key>` already delegates to these helpers - new UI that toggles a setting should just call it (fire-and-forget `fetch` is the idiom, see `themePickerForm()` in `settings_appearance.html` and `dashboardPrefsForm()` in `dashboard/index.html`).
- In tests that call `set_setting`/`delete_setting`, **always `cache.clear()` in `tearDown`**. Django's `LocMemCache` is process-global and is NOT reset between `TestCase` runs, so leaked cache entries can cause order-dependent failures.

```python
from django.core.cache import cache

class MyTests(TestCase):
    def tearDown(self):
        cache.clear()
```

### Logging - sanitize user-controlled values with `scrub()`

Any value taken from request data, request headers, URL path/query, filenames, DB rows that originated in user input (a stored push-subscription endpoint, a free-text title, an email/domain, etc.), or third-party API responses **must** pass through `scrub()` before reaching a `logger.X(...)` call. This prevents log injection (CWE-117): without it, `\r\n` in user input forges fake log lines and breaks SIEM parsers.

```python
from workspace.common.logging import scrub

logger.info("Autodiscover failed for domain %s", scrub(domain))
logger.warning("Push failed for %s: %s", scrub(sub.endpoint[:60]), e)
logger.exception("Activity provider '%s' failed", scrub(source))
```

**Rules:**

- Sanitize at the logger call site even when the value looks "safe" (a validated UUID, an enum slug, an email that passed `EmailField`). Validation runs at the view boundary, but the same value flows through Celery tasks, signals, and services to loggers far from where it was checked. CodeQL traces taint, not validation - the `py/log-injection` alert fires regardless.
- Never log full request bodies or headers. If you must, scrub them.
- Internal/system values that never touched user input (settings keys, hard-coded enum members, `__name__`, computed counts) don't need `scrub()`. Apply it to the *tainted* fields, not the whole format string.
- The helper lives in `workspace/common/logging.py`. The `str(...).replace('\r','').replace('\n','')` chain inside is the exact form CodeQL recognizes as a sanitizer for `py/log-injection` - do not refactor the replaces away or wrap them in another helper.

### Query parameter parsing - never trust raw values from `request.query_params` or `request.data`

Two recurring bugs land here, both because Python's loose typing or Django's deep-cleaning layer surface as confusing 500s instead of clean 4xxs:

**UUID parameters - validate at the boundary.** Passing a raw string straight to `Model.objects.get(uuid=...)`, `filter(uuid=...)`, or `Q(...uuid=...)` lets `UUIDField.to_python` raise `ValidationError` deep inside Django's cleaning layer. The surrounding `except Model.DoesNotExist` does **not** catch it - the exception escapes the view as a 500. Use `workspace.common.uuids.parse_uuid_or_none` instead:

```python
from workspace.common.uuids import parse_uuid_or_none

account_id = request.query_params.get('account')
if account_id:
    account_uuid = parse_uuid_or_none(account_id)
    if account_uuid is None:
        return Response(status=status.HTTP_404_NOT_FOUND)  # or 400 for collection filters
    try:
        account = MailAccount.objects.get(uuid=account_uuid, owner=request.user)
    except MailAccount.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)
```

When the UUID identifies a single resource (folder, account, label by id), map malformed input to **404** - the resource doesn't exist either way and 404 avoids leaking "format invalid" vs "not found". When it's a collection filter (e.g. `?account_id=` on a search endpoint), prefer **400** so the client sees the bug. URL kwargs declared with the `<uuid:>` path converter (e.g. `path('.../<uuid:uuid>', ...)`) are validated by Django at routing time, so they don't need this helper.

**Boolean parameters - never use Python truthiness on a string.** `if request.query_params.get('unread'):` is wrong: a non-empty string like `'false'` or `'0'` is truthy in Python, so a URL like `?unread=false` *enables* the filter the user is trying to disable. Use `workspace.common.booleans.is_truthy`, which mirrors DRF's `BooleanField.TRUE_VALUES`:

```python
from workspace.common.booleans import is_truthy

if is_truthy(request.query_params.get('unread')):
    qs = qs.filter(is_read=False)
```

Accepted true values: `true`, `1`, `yes`, `on`, `t`, `y` (case-insensitive). Everything else - including unknown strings, empty, `None`, and the false values - yields `False`. Permissive on purpose: a malformed boolean shouldn't 400 a search endpoint.

**Rules:**

- Every `objects.get(uuid=<request_value>)` or `filter(uuid=<request_value>)` in a view, AI tool args, or SSE handler must validate via `parse_uuid_or_none` first - unless the value is already typed (DRF serializer `UUIDField`, Pydantic `UUID`, URL `<uuid:>` converter).
- Every `if request.query_params.get('<flag>'):` that gates a filter or feature must use `is_truthy(...)` instead.
- For Pydantic-backed AI tool args, type the field as `uuid.UUID` rather than `str` so Pydantic rejects garbage at the tool-call boundary with a diagnostic error.

### Copying file content between rows - never assign a `FieldFile` directly

When duplicating an existing file/attachment into a new row (chat `save-to-files`, mail `save-to-files`, files `copy_node`, anything similar), **never** assign the source `FieldFile` straight to the destination model's `FileField`. Doing so makes both rows point at the same blob in storage, so deleting the source later silently breaks the destination.

The mechanism: Django's `FileField.pre_save` only invokes `storage.save()` (which generates a fresh storage path) when the value's `_committed` attribute is `False`. `FieldFile` (the descriptor returned for an existing row's `FileField`) carries `_committed=True`, so assigning it as-is is treated as "already in storage, do nothing" - the destination row gets the source's path verbatim.

```python
# ❌ Both rows now share the same blob. Delete the source -> destination orphans.
new_file.content = source.content                         # FieldFile, committed
new_file.save()
```

The fix is to wrap in `django.core.files.File` (or any other non-`FieldFile` `File` subclass: `ContentFile`, `UploadedFile`, ...). Those default to `_committed=False`, so `storage.save()` runs and streams the source via `content.chunks()` (default 64KB blocks) into a fresh path. This handles streaming AND the copy-correctness invariant in one move.

```python
# ✅ Streamed copy into a fresh storage path.
from django.core.files.base import File as DjangoFile

with source.content.open('rb') as f:
    new_file.content = DjangoFile(f, name=source.name)
    new_file.save()
```

**Rules:**

- Always pin this with a regression test that asserts `dest.content.name != source.content.name` after the copy AND that the bytes round-trip. Don't rely on a "content equality" check alone - the buggy version with a shared blob also passes a content check (it's the same blob).
- Wrap the open + save in `try/except (FileNotFoundError, OSError)` whenever copying user-uploaded content. A vanished blob otherwise surfaces as a bare 500 with no breadcrumbs. Mirror the response code of the closest read endpoint (404 for chat / mail attachment paths) and log the path through `scrub()` before re-raising or returning.
- `ContentFile(source.read(), ...)` happens to be _committed=False so it copies correctly, but it buffers the entire file in memory before re-emitting it. For anything that could grow (>1MB), prefer the `DjangoFile(open_stream, ...)` idiom.
- Existing precedent in the codebase: `workspace/files/webdav/resources.py:_copy_as` (already correct), `workspace/chat/views_attachments.py:AttachmentSaveToFilesView`, `workspace/mail/views.py:MailAttachmentSaveToFilesView`, `workspace/files/services/_storage_ops.py:copy_node`.

## Frontend Conventions

### Alpine `init()` is auto-called - never add `x-init="init()"` on top of it

If your `x-data` component defines an `init()` method (`x-data="myApp()"` where `myApp` returns an object with `init() { ... }`), Alpine **automatically** invokes it when the element mounts. Adding `x-init="init()"` next to `x-data="myApp()"` runs `init()` a **second time**, silently:

```html
<!-- ❌ WRONG - init() runs twice -->
<div x-data="chatApp()" x-init="init()"></div>

<!-- ✅ Correct - Alpine auto-calls init() once -->
<div x-data="chatApp()"></div>
```

The bug is invisible: the second pass overwrites the first with the same data, no console warning, no broken UI. The visible cost is **double API calls and double event-listener registration** for everything in `init()`. We hit this in 4 modules (chat, mail, notes, dashboard) and the only diagnostic was a network-level audit.

**Rules:**
- Component objects with an `init()` method must rely on Alpine's auto-call. Do **not** also write `x-init="init()"`.
- `x-init` is only for **inline expressions** on components that don't define an `init()` method (e.g., `<div x-data="{ open: false }" x-init="$watch('open', ...)">`).
- When adding event listeners inside `init()`, remember they will be added once per mount - if you ever do see two listeners firing, suspect a duplicate `x-init` or a duplicate `x-data` instantiation of the same component (see `filePreferences()` in `files/ui/index.html`, instantiated twice intentionally - its `init()` should be guarded against re-fetching).

### Embedding view data into JS - use `|json_script`, never `orjson.dumps + |safe`

When a Django view needs to hand off data to client-side JS (initial state, server-rendered preferences, serialized querysets that would otherwise force a redundant API call), **pass the raw Python object in context** and render it with Django's built-in `|json_script` filter:

```python
# View - pass the raw dict/list (NOT a JSON string)
return render(request, 'mail/ui/index.html', {
    'accounts': MailAccountSerializer(accounts, many=True).data,
    'oauth_providers': get_available_providers(),
})
```

```django
{# Template - |json_script renders <script id="..." type="application/json">...</script> #}
{{ accounts|json_script:"accounts-data" }}
{{ oauth_providers|json_script:"oauth-providers-data" }}
```

```js
// JS - read from the DOM
const accounts = JSON.parse(document.getElementById('accounts-data').textContent);
const providers = JSON.parse(document.getElementById('oauth-providers-data').textContent);
```

**Never** do this:

```python
# ❌ Manual dump in the view
'accounts_json': orjson.dumps(serializer.data).decode(),
```
```django
{# ❌ Inline raw JSON via |safe - XSS surface, no auto-escaping of </script> #}
<script id="accounts-data" type="application/json">{{ accounts_json|safe }}</script>
```

**Why `|json_script` is mandatory here:**
- It escapes `<`, `>`, `&`, `'`, `\u2028`, `\u2029` as JS-safe Unicode escapes - `</script>` injection is impossible even if the data contains user-controlled strings. Manual `|safe` defeats Django's auto-escape entirely; you'd have to remember to do `.replace('</', '<\\/')` everywhere (and inevitably forget once).
- It produces a `<script type="application/json">` block, which the browser parses as data, not code - no eval, no parser tricks.
- It's built into Django (since 2.1) - no extra import, no `orjson`/`json` boilerplate in the view.

**Naming convention - drop `_json` from context variable names:** the value passed to the template is now a Python dict/list, not a JSON string. Naming it `accounts_json` is a lie. Always name the context variable for what it *is*:

| ❌ Old name | ✅ New name | Type at the view boundary |
|---|---|---|
| `accounts_json` | `accounts` | dict / list |
| `prefs_json` | `prefs` | dict |
| `calendars_json` | `calendars` | dict |
| `folders_json` | `folders` | list |

The script tag's `id` attribute is the right place for the `*-data` suffix (e.g., `id="accounts-data"`), not the Python context key.

**Exception** - when a context variable name collides with another already in context (e.g., a view passes both a queryset of accounts and the serialized version), check whether the queryset version is actually used in the template. It is often **dead context** (the template only reads `accounts` from the JS side via the embedded script tag). If so, delete the dead key; don't keep both.

### Server-rendered partial swaps - use alpine-ajax, never raw `fetch`

Whenever a piece of UI needs to be refreshed from a Django partial (lists, feeds, sidebars, popovers, folder trees, anything rendered server-side), **use [alpine-ajax](https://alpine-ajax.js.org)**. The library is already loaded globally in `base.html`.

**Never** write a hand-rolled `fetch(...).then(r.text()).then(html => el.innerHTML = html)`, `DOMParser` parsing of an HTML response, or `target.replaceWith(newNode)` + `Alpine.initTree()` pipeline. That pattern silently destroys every Alpine binding inside the swapped subtree (context menus, drag-and-drop handlers, `x-show` state, `x-model` bindings) because raw `innerHTML` assignment doesn't morph - it rebuilds the tree from scratch.

#### How to trigger a swap

**Declarative (user-triggered):** put `x-target="<target-id>"` on the `<a>` / `<form>` / `<button>` the user interacts with. The response must contain an element with matching `id`.

```html
<a href="{% url 'chat_ui:conversation_list' %}" x-target="conversation-list">Refresh</a>

<div id="conversation-list">
  {% include "chat/ui/partials/conversation_list.html" %}
</div>
```

**Programmatic (from an Alpine expression or a component method):** use the `$ajax(url, options)` magic. This is the **only** supported way to initiate a request from JavaScript - do not fake a user click on a hidden link.

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

Return the partial template directly - no JSON envelope, no wrapping. The endpoint often checks `request.headers.get('X-Alpine-Request')` so the same URL can serve the full page (browser refresh) and the fragment (alpine-ajax swap). Existing examples: `workspace/chat/ui/views.py:conversation_list_view`, `workspace/users/ui/views.py:profile_activity_feed`, `workspace/files/ui/views.py` (the `#folder-browser` branch).

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

- Context menus: fetch the action list for the target file(s) via `/api/v1/files/actions` and render only the returned actions - never render a static list of menu items.
- Buttons, links, inline inputs (e.g., title input for rename): bind their `:disabled` / `:readonly` attribute to the presence of the corresponding action ID in the fetched list. Default to disabled while the list is loading (fail-safe).
- When implementing a new file-manipulating UI element, first check that an `is_available` entry exists for the action in `workspace/files/actions/`. If not, add it - don't ship the UI without it.
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

Use a race-protection counter (see `_loadGeneration` in `workspace/notes/ui/static/notes/ui/js/notes.js:557`) when the fetched list feeds reactive state that depends on the current selection - rapid selection changes otherwise lead to stale results being applied.

**Scope:** applies to the `files` module and every module whose UI manipulates files (notes, mail attachments, chat attachments, etc.). If a module manipulates another kind of entity with its own action registry (not files), follow the same principle against that module's equivalent endpoint.
