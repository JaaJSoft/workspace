"""Per-user notification level resolution for project fan-out.

A level scopes how project notifications reach a user: ``all`` delivers
in-app and web push, ``in_app`` delivers everything except push, ``none``
drops the user from the recipient set. A ``ProjectNotificationLevel`` row
wins over the module-wide ``projects.notify_level`` user setting; absent or
malformed values resolve to ``all``. The notifications module stays
preference-agnostic - every producer filters its recipients here before
calling ``notify_stream``.
"""

from ..models import ProjectNotificationLevel

Level = ProjectNotificationLevel.Level

# The one priority send_push_notification is never dispatched for, on both
# the create and the merge path of notify_stream.
IN_APP_PRIORITY = "low"


def module_level(user):
    """The user's module-wide level (``projects.notify_level`` setting).

    The setting stores arbitrary JSON, so anything unrecognised falls back
    to ``all`` rather than silencing notifications by accident.
    """
    from workspace.users.services.settings import get_setting

    raw = get_setting(user, "projects", "notify_level", default=Level.ALL)
    return raw if raw in Level.values else Level.ALL


def user_levels(project_id, users):
    """Resolved level for each of *users* on the project -> {user_id: level}."""
    overrides = dict(
        ProjectNotificationLevel.objects.filter(
            project_id=project_id, user__in=[u.pk for u in users]
        ).values_list("user_id", "level")
    )
    return {u.pk: overrides.get(u.pk, module_level(u)) for u in users}


def apply_levels(project_id, recipients, *, priority_map=None):
    """Filter *recipients* by their level, ready for ``notify_stream``.

    Returns ``(recipients, priority_map)``: ``none`` users are dropped and
    ``in_app`` users get ``IN_APP_PRIORITY`` so no web push ever fires -
    including for mentions, whose elevated default the cap overrides.
    """
    levels = user_levels(project_id, recipients)
    kept = [u for u in recipients if levels[u.pk] != Level.NONE]
    priority_map = dict(priority_map or {})
    for user in kept:
        if levels[user.pk] == Level.IN_APP:
            priority_map[user.pk] = IN_APP_PRIORITY
    return kept, priority_map
