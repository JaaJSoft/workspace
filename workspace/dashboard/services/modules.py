from dataclasses import asdict

from workspace.core.services.module_visibility import visible_modules
from workspace.notifications.services.notifications import get_unread_badges
from workspace.users.services.settings import get_module_settings


def dashboard_modules(user):
    """Build the module tiles for the home page grid and the navbar switcher.

    Returns ``(modules, dashboard_apps)`` where ``modules`` is the visible grid
    (hidden slugs and the dashboard tile excluded, unread notification counts
    attached) and ``dashboard_apps`` is every visible app with a ``hidden``
    flag for the settings popover.

    A tile links to its module home unless the module has exactly one unread
    notification with a url, in which case it opens that item directly (the
    unread conversation, the due task, ...).
    """
    badges = get_unread_badges(user)
    hidden = set(get_module_settings(user, "dashboard").get("hidden_modules") or [])
    modules = []
    dashboard_apps = []
    for m in visible_modules(user):
        if m.slug == "dashboard" or not m.show_on_dashboard:
            continue
        dashboard_apps.append(
            {
                "slug": m.slug,
                "name": m.name,
                "icon": m.icon,
                "color": m.color,
                "hidden": m.slug in hidden,
            }
        )
        if m.slug in hidden:
            continue
        badge = badges.get(m.slug)
        data = asdict(m)
        data["notification_count"] = badge["count"] if badge else 0
        data["url"] = (badge["url"] if badge else None) or m.url
        modules.append(data)
    return modules, dashboard_apps
