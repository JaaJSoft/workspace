from collections import defaultdict
from datetime import timedelta

from ..models import TaskStatus

SCALES = ("week", "month", "quarter")
DEFAULT_SCALE = "week"
GROUPINGS = ("milestone", "epic")
DEFAULT_GROUPING = "milestone"

# One axis unit per scale: the padding on each side of the data extent.
_UNIT_DAYS = {"week": 7, "month": 30, "quarter": 90}
# Half of the 3-year window the chart will draw at most; anything further
# from today is clipped and counted rather than stretching the SVG.
_CLAMP_DAYS = 548

_CATEGORY_CSS = {
    TaskStatus.Category.DONE: "fill-success",
    TaskStatus.Category.ACTIVE: "fill-accent",
    TaskStatus.Category.BACKLOG: "fill-neutral",
}
_OVERDUE_CSS = "fill-error"
_MILESTONE_CSS = "fill-warning"
_CLOSED_MILESTONE_CSS = "fill-base-content/30"
_SPRINT_BAND_CSS = "fill-info/10"


def coerce_scale(value):
    return value if value in SCALES else DEFAULT_SCALE


def coerce_grouping(value):
    return value if value in GROUPINGS else DEFAULT_GROUPING


def build_timeline(tasks, milestones, sprints, *, group, scale, today):
    """Chart-ready groups, markers and bands for a project's timeline.

    *tasks* are already filtered and capped, with ``status``, ``epic`` and
    ``milestone`` loaded; *milestones* come from milestones_with_progress;
    *sprints* are the project's sprints (empty for non-scrum projects).
    Returns the extent the caller hands to gantt_chart plus the counts of
    tasks it could not place (undated ones, and ones outside the extent) and
    the count of milestones whose target date falls outside the extent -
    they still ride along in ``markers``, but gantt_chart drops them.
    """
    rows_by_task = [(task, _row(task, today)) for task in tasks]
    undated_count = sum(1 for _, row in rows_by_task if row is None)
    placed = [(task, row) for task, row in rows_by_task if row is not None]

    dates = [today]
    for _, row in placed:
        dates.append(row["start"])
        if row["end"] is not None:
            dates.append(row["end"])
    dates.extend(m.target_date for m in milestones)
    bands = _bands(sprints)
    for band in bands:
        dates.extend((band["start"], band["end"]))
    unit = timedelta(days=_UNIT_DAYS[scale])
    start = max(min(dates) - unit, today - timedelta(days=_CLAMP_DAYS))
    end = min(max(dates) + unit, today + timedelta(days=_CLAMP_DAYS))

    visible = [(task, row) for task, row in placed if _overlaps(row, start, end)]
    clipped_count = len(placed) - len(visible)
    clipped_milestone_count = sum(
        1 for m in milestones if not (start <= m.target_date <= end)
    )
    visible.sort(
        key=lambda pair: (pair[1]["start"], pair[1]["end"] or pair[1]["start"])
    )

    if group == "epic":
        groups = _epic_groups(visible)
    else:
        groups = _milestone_groups(visible, milestones)

    markers = [
        {
            "label": m.name,
            "date": m.target_date,
            "css_class": _CLOSED_MILESTONE_CSS if m.is_closed else _MILESTONE_CSS,
        }
        for m in milestones
    ]
    return {
        "start": start,
        "end": end,
        "groups": groups,
        "markers": markers,
        "bands": bands,
        "undated_count": undated_count,
        "clipped_count": clipped_count,
        "clipped_milestone_count": clipped_milestone_count,
    }


def _row(task, today):
    if task.start_date is None and task.due_date is None:
        return None
    if task.start_date is not None and task.due_date is not None:
        start, end, kind = task.start_date, task.due_date, "bar"
    else:
        start, end, kind = task.start_date or task.due_date, None, "marker"
    done = task.status.category == TaskStatus.Category.DONE
    overdue = not done and task.due_date is not None and task.due_date < today
    parts = [task.reference]
    if task.start_date is not None:
        parts.append(f"from {task.start_date.strftime('%b %d')}")
    if task.due_date is not None:
        parts.append(f"due {task.due_date.strftime('%b %d')}")
    if overdue:
        parts.append("overdue")
    return {
        "id": str(task.uuid),
        "label": f"{task.reference} {task.title}",
        "start": start,
        "end": end,
        "kind": kind,
        "css_class": _OVERDUE_CSS if overdue else _CATEGORY_CSS[task.status.category],
        "tooltip": f"{task.title} · " + ", ".join(parts) + f" · {task.status.name}",
    }


def _overlaps(row, start, end):
    last = row["end"] or row["start"]
    return row["start"] <= end and last >= start


def _bands(sprints):
    return [
        {
            "label": sprint.name,
            "start": sprint.start_date,
            "end": sprint.end_date,
            "css_class": _SPRINT_BAND_CSS,
        }
        for sprint in sprints
        if sprint.start_date is not None and sprint.end_date is not None
    ]


def _milestone_groups(visible, milestones):
    """One group per milestone in date order, then "No milestone".

    A milestone with no visible task keeps its (empty) group so its header
    still reads on the chart; the trailing group only appears when needed.
    """
    by_milestone = defaultdict(list)
    for task, row in visible:
        by_milestone[task.milestone_id].append(row)
    groups = [
        {
            "label": m.name,
            "sublabel": m.target_date.strftime("%b %d"),
            "progress": f"{m.done_task_count}/{m.task_count}",
            "rows": by_milestone.get(m.pk, []),
        }
        for m in milestones
    ]
    if by_milestone.get(None):
        groups.append(
            {
                "label": "No milestone",
                "sublabel": "",
                "progress": "",
                "rows": by_milestone[None],
            }
        )
    return groups


def _epic_groups(visible):
    by_epic = defaultdict(list)
    epics = {}
    for task, row in visible:
        by_epic[task.epic_id].append(row)
        if task.epic_id is not None:
            epics[task.epic_id] = task.epic
    groups = [
        {"label": epic.name, "sublabel": "", "progress": "", "rows": by_epic[pk]}
        for pk, epic in sorted(epics.items(), key=lambda item: item[1].name.lower())
    ]
    if by_epic.get(None):
        groups.append(
            {"label": "No epic", "sublabel": "", "progress": "", "rows": by_epic[None]}
        )
    return groups
