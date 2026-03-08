# Claude Code Instructions

## Git

Never commit automatically. Only commit when I explicitly ask for it.
Do not use git worktrees. Work directly on the current branch.

## API

all api must be prefixed with `/api/` and have no trailing slashes

## UI Components

Always use the existing UI partials located in `workspace/common/templates/ui/partials/` instead of writing inline HTML for common components.

### Alerts

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

### Dialogs

Use the `dialogs` partial for modal dialogs instead of inline modal HTML.

### Other Available Partials

- `app_logo.html` - Application logo
- `breadcrumbs.html` - Breadcrumb navigation
- `navbar.html` - Navigation bar
- `user_avatar.html` - User avatar display

## Access Control Querysets

Never duplicate access/permission querysets. Always use the centralized helpers below. This ensures permission logic is defined once per module.

### Chat — `user_conversation_ids(user)`

```python
from workspace.chat.services import user_conversation_ids
conv_ids = user_conversation_ids(user)  # returns queryset of conversation UUIDs
```

Returns conversation UUIDs where the user is an active member (`left_at__isnull=True`).

### Mail — `user_account_ids(user)`

```python
from workspace.mail.queries import user_account_ids
account_ids = user_account_ids(user)  # returns queryset of account UUIDs
```

Returns mail account UUIDs owned by the user. Use for filtering messages: `MailMessage.objects.filter(account_id__in=account_ids, ...)`.

### Calendar — `visible_calendar_ids(user)` / `visible_events_q(user)`

```python
from workspace.calendar.queries import visible_calendar_ids, visible_events_q

# For calendar-level queries (owned + subscribed):
cal_ids = visible_calendar_ids(user)

# For event-level queries (owned calendars + subscribed calendars + event membership):
events = Event.objects.filter(visible_events_q(user), title__icontains=query)
```

### Files — `FileService.user_files_qs(user)`

```python
from workspace.files.services import FileService
qs = FileService.user_files_qs(user)  # returns queryset: File(owner=user, deleted_at__isnull=True)
```

Returns active (non-deleted) files owned by the user. For single-file access checks, use `FileService.can_access(user, file_obj)` instead.
