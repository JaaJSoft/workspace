# Claude Code Instructions

## Workflow

### Git

- Never commit automatically. Only commit when I explicitly ask for it.
- Do not use git worktrees. Work directly on the current branch.

### Refactoring & Optimization

Before any refactor or optimization, verify that at least one test covers the code being touched. If no test exists, **write the test first** (it must pass against the current code), then start the refactor. The test acts as a safety net to guarantee the behavior is preserved.

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

## Frontend Conventions

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
- `user_avatar.html` - User avatar display
