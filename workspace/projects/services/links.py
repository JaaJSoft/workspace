"""Task-to-task links: creation rules, per-viewer serialization, blocked cue."""

from django.db.models import Exists, OuterRef, Q

from ..models import TaskEvent, TaskLink, TaskStatus
from ..queries import user_project_ids
from .events import record_task_event
from .members import ProjectRuleError

# Relation names the API accepts, from the anchor task's perspective. A
# reversed relation stores the canonical type with the ends swapped, so
# "A is blocked by B" and "B blocks A" land on the same row.
RELATIONS = {
    "blocks": (TaskLink.Type.BLOCKS, False),
    "blocked_by": (TaskLink.Type.BLOCKS, True),
    "duplicates": (TaskLink.Type.DUPLICATES, False),
    "duplicated_by": (TaskLink.Type.DUPLICATES, True),
    "relates_to": (TaskLink.Type.RELATES_TO, False),
}

# Display labels per canonical type: (source perspective, target perspective).
LINK_LABELS = {
    TaskLink.Type.BLOCKS: ("blocks", "is blocked by"),
    TaskLink.Type.DUPLICATES: ("duplicates", "is duplicated by"),
    TaskLink.Type.RELATES_TO: ("relates to", "relates to"),
}


def create_link(anchor, other, relation, *, actor=None):
    """Link *anchor* to *other* per *relation* (a RELATIONS key).

    Raises ProjectRuleError on a self-link, an existing same-type link in
    either direction, or a ``blocks`` link that would close a cycle. Both
    tasks must carry their project in cache (the events snapshot references).
    """
    canonical, is_reversed = RELATIONS[relation]
    source, target = (other, anchor) if is_reversed else (anchor, other)
    if source.pk == target.pk:
        raise ProjectRuleError("A task cannot be linked to itself.")
    if TaskLink.objects.filter(
        Q(source=source, target=target) | Q(source=target, target=source),
        type=canonical,
    ).exists():
        raise ProjectRuleError("These tasks are already linked with this type.")
    if canonical == TaskLink.Type.BLOCKS and _reaches_through_blocks(target, source):
        raise ProjectRuleError("This link would make a task block itself.")
    link = TaskLink.objects.create(
        source=source, target=target, type=canonical, created_by=actor
    )
    _record_link_events(link, TaskEvent.Type.LINKED, actor)
    return link


def delete_link(link, *, actor=None):
    """Remove *link*, leaving an UNLINKED event on both ends."""
    _record_link_events(link, TaskEvent.Type.UNLINKED, actor)
    link.delete()


def _reaches_through_blocks(start, wanted):
    """True when *wanted* is reachable from *start* along ``blocks`` edges.

    Breadth-first, one query per depth level: blocking chains are shallow in
    practice, and a recursive CTE would not be portable to SQLite through
    the ORM.
    """
    seen = {start.pk}
    frontier = [start.pk]
    while frontier:
        step = set(
            TaskLink.objects.filter(
                type=TaskLink.Type.BLOCKS, source_id__in=frontier
            ).values_list("target_id", flat=True)
        )
        if wanted.pk in step:
            return True
        frontier = list(step - seen)
        seen |= step
    return False


def _record_link_events(link, event_type, actor):
    """One event per end, so both activity feeds carry the change.

    The direction label and the other end's reference are snapshotted in the
    value fields, so the entry stays readable after either task is deleted.
    """
    forward, backward = LINK_LABELS[TaskLink.Type(link.type)]
    for task, label, other in (
        (link.source, forward, link.target),
        (link.target, backward, link.source),
    ):
        record_task_event(
            task,
            type=event_type,
            actor=actor,
            from_value=label,
            to_value=other.reference,
        )


def links_for_task(user, task):
    """Serialize *task*'s links for one viewer, anchored on *task*.

    Both directions fold into one list; a link whose other end sits in a
    project the viewer cannot access is dropped entirely - its existence
    must not leak.
    """
    accessible = None
    items = []
    links = (
        TaskLink.objects.filter(Q(source=task) | Q(target=task))
        .select_related(
            "source__project", "source__status", "target__project", "target__status"
        )
        .order_by("created_at")
    )
    for link in links:
        outward = link.source_id == task.pk
        other = link.target if outward else link.source
        if other.project_id != task.project_id:
            if accessible is None:
                accessible = set(user_project_ids(user))
            if other.project_id not in accessible:
                continue
        forward, backward = LINK_LABELS[TaskLink.Type(link.type)]
        items.append(
            {
                "uuid": str(link.uuid),
                "type": link.type,
                "label": forward if outward else backward,
                "task": {
                    "uuid": str(other.uuid),
                    "reference": f"{other.project.key}-{other.number}",
                    "title": other.title,
                    "project": str(other.project_id),
                    "is_done": other.status.category == TaskStatus.Category.DONE,
                    "url": f"/projects/{other.project_id}?task={other.uuid}",
                },
            }
        )
    return items


def annotate_blocked(qs):
    """Annotate a task queryset with ``is_blocked``: some open (not done)
    task blocks it. Done blockers don't count - a resolved dependency no
    longer stands in the way."""
    open_blockers = TaskLink.objects.filter(
        type=TaskLink.Type.BLOCKS, target=OuterRef("pk")
    ).exclude(source__status__category=TaskStatus.Category.DONE)
    return qs.annotate(is_blocked=Exists(open_blockers))
