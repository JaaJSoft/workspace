# Dashboard App Grid with Badge Counts

**Date:** 2026-02-24
**Status:** Approved

## Goal

Replace the current modules grid on the dashboard with a compact dock-style app launcher. Each module tile displays a notification badge showing the count of pending/actionable items specific to that module's domain.

## Architecture: Badge Providers in Module Registry

Each module registers a **badge provider** function in the module registry (same pattern as `SearchProviderInfo`). The dashboard view collects all badge counts in a single call and passes them to the template.

### New dataclass

```python
@dataclass(frozen=True)
class BadgeProviderInfo:
    module_slug: str
    badge_fn: Callable  # (user) -> int
```

### New registry methods

- `register_badge_provider(provider)` - called by each app in `ready()`
- `get_badge_counts(user) -> dict[str, int]` - calls all providers, returns `{slug: count}`

## Badge Logic per Module

| Module | What it counts | Provider location |
|--------|---------------|-------------------|
| Files | No badge | - |
| Chat | Unread messages (total) | `chat/apps.py` |
| Calendar | Events in next 7 days | `calendar/apps.py` |
| Mail | Unread emails | `mail/apps.py` |
| Dashboard | No badge (is the current page) | - |

## UI Design

Compact dock-style horizontal grid:
- Square tiles with centered icon in colored circle
- Module name below
- Badge positioned absolute top-right corner (only when count > 0)
- Badge uses module's accent color
- Inactive modules (Notes, Tasks) keep dashed border + "Coming soon"
- Responsive: wraps on mobile

## Integration

- `dashboard/views.py`: `_build_dashboard_context()` calls `registry.get_badge_counts(user)` and adds `module_badges` to context
- Badge counts are computed at page load only (no SSE real-time updates)
- No new API endpoints needed

## Files Changed

| File | Change |
|------|--------|
| `workspace/core/module_registry.py` | Add `BadgeProviderInfo`, `register_badge_provider()`, `get_badge_counts()` |
| `workspace/chat/apps.py` | Register badge provider (unread messages) |
| `workspace/calendar/apps.py` | Register badge provider (upcoming events 7d) |
| `workspace/mail/apps.py` | Register badge provider (unread emails) |
| `workspace/dashboard/views.py` | Call `get_badge_counts()` in context builder |
| `workspace/dashboard/templates/dashboard/partials/modules_grid.html` | Redesign to compact dock with badges |
