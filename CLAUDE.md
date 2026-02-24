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
