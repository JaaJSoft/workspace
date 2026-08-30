from dataclasses import asdict

from workspace.core.services.module_visibility import visible_modules
from workspace.notifications.services.notifications import get_unread_badges
from workspace.users.services.settings import get_module_settings


def dashboard_modules(user, *, deep_links=True):
    """Build the module tiles for the home page grid and the navbar switcher.

    Returns ``(modules, dashboard_apps)`` where ``modules`` is the visible grid
    (hidden slugs and the dashboard tile excluded, unread notification counts
    attached) and ``dashboard_apps`` is every visible app with a ``hidden``
    flag for the settings popover.

    A tile links to its module home unless ``deep_links`` is true and the
    module has exactly one unread notification with a url, in which case it
    opens that item directly (the unread conversation, the due task, ...).
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
        data["url"] = (
            ((badge["url"] if badge else None) or m.url) if deep_links else m.url
        )
        modules.append(data)
    return modules, dashboard_apps


def switcher_modules_for(user, current):
    """Tiles for the navbar module switcher.

    The home grid filtered by the user's preferences, linked to each module's
    home rather than to a single unread item, and always containing
    *current* (the module the page belongs to) even when the grid dropped it
    (kept off the dashboard, or hidden by the user's preferences).
    """
    modules, _ = dashboard_modules(user, deep_links=False)
    if current is not None and all(m["slug"] != current.slug for m in modules):
        badge = get_unread_badges(user).get(current.slug)
        modules.append(
            {**asdict(current), "notification_count": badge["count"] if badge else 0}
        )
    return modules
