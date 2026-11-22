import time

from django.core.cache import cache
from django.db import transaction

from workspace.core.sse_registry import MailboxSSEProvider, push_user_event

SLUG = "projects"
# The stream prefixes it with the slug: pages listen to sse:projects.changed.
CHANGED = "changed"

_REVISION_KEY = "projects:revision:{project_uuid}"


class ProjectsSSEProvider(MailboxSSEProvider):
    slug = SLUG


def _revision_floor():
    # Counted from the clock in microseconds, so a counter the cache lost
    # restarts above every revision a page already shows instead of below it.
    return time.time_ns() // 1000


def project_revision(project_uuid):
    """The newest change announced for the project.

    A view reads it before anything else it loads, so the page it renders
    shows at least every change up to that revision: an event announcing one
    at or below it is already on screen.
    """
    return cache.get(_REVISION_KEY.format(project_uuid=project_uuid)) or (
        _revision_floor()
    )


def _next_revision(project_uuid):
    key = _REVISION_KEY.format(project_uuid=project_uuid)
    cache.add(key, _revision_floor(), timeout=None)
    try:
        return cache.incr(key)
    except ValueError:
        # Evicted between the add and the incr.
        cache.add(key, _revision_floor(), timeout=None)
        return cache.incr(key)


def broadcast_project_change(project, *, task_uuid=None):
    """Tell the open pages of every user who can reach *project* that it changed.

    Sent once the write commits, to every tab of every member, the author's
    included: their other tabs must follow too, and the tab that made the
    change skips an event its own refresh already covers. The event names
    what changed and never its content; each page re-renders through its own
    access-checked views.
    """
    transaction.on_commit(
        lambda: _push_change(project, task_uuid),
        # A failed push must not turn a write that committed into an error.
        robust=True,
    )


def _push_change(project, task_uuid):
    from workspace.projects.queries import project_users

    event = {
        "type": CHANGED,
        "project": str(project.uuid),
        "task": str(task_uuid) if task_uuid else None,
        "revision": _next_revision(project.uuid),
    }
    for user in project_users(project):
        push_user_event(SLUG, user.pk, event)
