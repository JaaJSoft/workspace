from django.utils.translation import gettext as _
from django.utils.translation import pgettext_lazy

from ..models import TaskEvent, TaskStatus
from ..queries import visible_task_events_q

# The direction labels links.py snapshots into a link event's from_value, in
# English: the stored value is data, so it is translated when it is read.
_LINK_DIRECTIONS = {
    "blocks": pgettext_lazy("task link", "blocks"),
    "is blocked by": pgettext_lazy("task link", "is blocked by"),
    "duplicates": pgettext_lazy("task link", "duplicates"),
    "is duplicated by": pgettext_lazy("task link", "is duplicated by"),
    "relates to": pgettext_lazy("task link", "relates to"),
}


def link_direction_label(value):
    """The reader's wording of a snapshotted link direction; an unknown one as stored."""
    return str(_LINK_DIRECTIONS.get(value, value))


def record_task_event(
    task,
    *,
    type,
    actor=None,
    from_status=None,
    to_status=None,
    from_value="",
    to_value="",
    from_ref=None,
    to_ref=None,
):
    """Insert one TaskEvent row, snapshotting the task title and the names
    and categories of the statuses involved. *from_ref*/*to_ref* carry the
    identity behind a name in *from_value*/*to_value* (a sprint's UUID)."""
    return TaskEvent.objects.create(
        project=task.project,
        task=task,
        task_title=task.title,
        task_number=task.number,
        actor=actor,
        type=type,
        from_status=from_status.name if from_status is not None else "",
        to_status=to_status.name if to_status is not None else "",
        from_category=from_status.category if from_status is not None else "",
        to_category=to_status.category if to_status is not None else "",
        from_value=from_value,
        to_value=to_value,
        from_ref=from_ref,
        to_ref=to_ref,
    )


def move_event_type(to_status):
    """Event type for a move into *to_status*: landing on a Done column is a
    completion, anything else (including reopening) is a plain move."""
    if to_status.category == TaskStatus.Category.DONE:
        return TaskEvent.Type.COMPLETED
    return TaskEvent.Type.MOVED


def events_for_project(user, project, limit=15):
    """Newest-first events of *project* that *user* may read, for the
    project overview card."""
    return project.task_events.filter(visible_task_events_q(user)).select_related(
        "actor", "project"
    )[:limit]


def serialize_task_event(ev):
    """Normalize a TaskEvent into the activity-feed event dict shape.

    Shared between the activity provider and the task detail panel so both
    render through core/partials/activity_item.html.
    """
    if ev.actor is not None:
        actor = {
            "id": ev.actor.pk,
            "username": ev.actor.username,
            "full_name": ev.actor.get_full_name(),
        }
    else:
        # Null actor means a system-driven write; never attribute it to
        # a real user (same convention as the files provider).
        actor = None
    # Deep-link to the task panel while the task exists; a deleted task
    # (SET_NULL on the event) falls back to the project page.
    url = f"/projects/{ev.project_id}"
    if ev.task_id is not None:
        url = f"{url}?task={ev.task_id}"
    description = ev.task_title
    if ev.task_number is not None:
        description = f"{ev.project.key}-{ev.task_number} · {ev.task_title}"
    label = ev.short_label
    if ev.type == TaskEvent.Type.ESTIMATED:
        if ev.from_value and ev.to_value:
            label = _("Estimate changed: %(old)s → %(new)s") % {
                "old": ev.from_value,
                "new": ev.to_value,
            }
        elif ev.to_value:
            label = _("Estimate set to %(value)s") % {"value": ev.to_value}
        elif ev.from_value:
            label = _("Estimate removed")
    elif ev.type == TaskEvent.Type.EPIC:
        # from_value/to_value carry the epic names snapshotted at change time.
        if ev.from_value and ev.to_value:
            label = _("Epic changed: %(old)s → %(new)s") % {
                "old": ev.from_value,
                "new": ev.to_value,
            }
        elif ev.to_value:
            label = _("Epic set to %(name)s") % {"name": ev.to_value}
        elif ev.from_value:
            label = _("Epic removed")
    elif ev.type == TaskEvent.Type.SPRINT:
        # from_value/to_value carry the sprint names snapshotted at change
        # time, same rationale as the epic names.
        if ev.from_value and ev.to_value:
            label = _("Sprint changed: %(old)s → %(new)s") % {
                "old": ev.from_value,
                "new": ev.to_value,
            }
        elif ev.to_value:
            label = _("Added to sprint %(name)s") % {"name": ev.to_value}
        elif ev.from_value:
            label = _("Removed from sprint %(name)s") % {"name": ev.from_value}
    elif ev.type in (TaskEvent.Type.LINKED, TaskEvent.Type.UNLINKED):
        # from_value holds the direction label ("blocks", "is blocked by"),
        # to_value the other end's reference, both snapshotted at link time.
        # Feeds filter with visible_task_events_q, which drops the event when
        # the viewer cannot access the other end's project.
        if ev.from_value and ev.to_value:
            label = _("%(event)s: %(direction)s %(reference)s") % {
                "event": ev.short_label,
                "direction": link_direction_label(ev.from_value),
                "reference": ev.to_value,
            }
    elif ev.type in (TaskEvent.Type.FILE_LINKED, TaskEvent.Type.FILE_UNLINKED):
        # to_value holds the file name snapshotted at link time.
        if ev.to_value:
            label = _("%(event)s: %(file)s") % {
                "event": ev.short_label,
                "file": ev.to_value,
            }
    return {
        "icon": ev.icon,
        "label": str(label),
        "description": description,
        "timestamp": ev.created_at,
        "url": url,
        "actor": actor,
    }
