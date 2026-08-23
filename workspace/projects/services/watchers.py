"""Task watch state: explicit subscriptions, mutes, and their fan-out.

A ``TaskWatcher`` row is the user's explicit stance on one task: watching
(``muted=False``) adds them to every recipient set the implicit rules would
build, muted (``muted=True``) removes them from it. No row means the
implicit rules alone decide. Mentions and assignment notifications are
personal and bypass the muted state; the per-project/module notification
levels still apply on top (``notification_levels``).
"""

from collections import defaultdict

from workspace.notifications.services.notifications import notify_stream

from ..models import TaskStatus, TaskWatcher
from ..queries import project_users
from .notification_levels import apply_levels


def set_watch_state(task, user, *, muted):
    """Upsert the user's explicit watch state on *task*."""
    watcher, created = TaskWatcher.objects.get_or_create(
        task=task, user=user, defaults={"muted": muted}
    )
    if not created and watcher.muted != muted:
        watcher.muted = muted
        watcher.save(update_fields=["muted"])
    return watcher


def clear_watch_state(task, user):
    """Drop the explicit state; the implicit rules decide again."""
    TaskWatcher.objects.filter(task=task, user=user).delete()


def auto_watch(task, users):
    """Subscribe *users* who opted into auto-watch (on comment/assignment).

    ``get_or_create`` keeps an existing muted row muted: auto-watch may
    never override an explicit opt-out.
    """
    from workspace.users.services.settings import get_setting

    for user in users:
        if get_setting(user, "projects", "auto_watch", default=True):
            TaskWatcher.objects.get_or_create(task=task, user=user)


def watch_states(tasks):
    """Watch rows for *tasks* -> {task_id: {user_id: muted}}."""
    states = defaultdict(dict)
    rows = TaskWatcher.objects.filter(task__in=tasks).values_list(
        "task_id", "user_id", "muted"
    )
    for task_id, user_id, muted in rows:
        states[task_id][user_id] = muted
    return states


def apply_watchers(task, recipients, allowed_by_id):
    """Union non-muted watchers into *recipients* and drop muted users.

    *allowed_by_id* maps user id -> User for everyone currently allowed to
    receive the task's notifications (project access, active); watchers who
    lost access since subscribing are skipped at send time.
    """
    states = watch_states([task]).get(task.pk, {})
    kept = {
        u.pk: u
        for u in recipients
        if u.pk in allowed_by_id and states.get(u.pk) is not True
    }
    for uid, muted in states.items():
        if not muted and uid in allowed_by_id:
            kept.setdefault(uid, allowed_by_id[uid])
    return list(kept.values())


def _task_url(task):
    return f"/projects/{task.project_id}/board?task={task.uuid}"


def notify_status_changed(moved, actor):
    """Notify each moved task's non-muted watchers - never the actor.

    *moved* is ``[(task, old_status)]`` within one project. Watchers are the
    whole audience here: status changes have no implicit recipient set, so
    only an explicit subscription opts into them. Keyed on the task with its
    own stream, so a task dragged across several columns merges into one
    unread row instead of stacking.
    """
    if not moved:
        return
    project = moved[0][0].project
    allowed = {u.pk: u for u in project_users(project) if u.is_active}
    states = watch_states([task for task, _ in moved])
    for task, _old_status in moved:
        watchers = [
            allowed[uid]
            for uid, muted in states.get(task.pk, {}).items()
            if not muted and uid in allowed and (actor is None or uid != actor.pk)
        ]
        recipients, priority_map = apply_levels(project.pk, watchers)
        if not recipients:
            continue
        who = actor.username if actor else "Someone"
        if task.status.category == TaskStatus.Category.DONE:
            title = f'{who} completed "{task.title}"'
        else:
            title = f'{who} moved "{task.title}" to {task.status.name}'
        notify_stream(
            recipient_ids=[u.pk for u in recipients],
            source=task,
            origin="projects",
            title=title,
            url=_task_url(task),
            actor=actor,
            priority_map=priority_map,
            stream="status",
        )
