"""Reports rebuilt from the task event log.

Cycle and lead time, the cumulative flow diagram and the sprint burndown
all ask what a task looked like at some past instant, which no table
holds: they replay the project's TaskEvent rows in order and read the
answer off the reconstructed state. Events outlive their task (SET_NULL
plus snapshots), so a deleted task keeps counting in what it took part in.

Tasks are keyed by the snapshotted task number, which survives deletion
and is never reused, so a deleted task's events still group together.
"""

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Sum
from django.utils import timezone

from ..models import Sprint, Task, TaskEvent, TaskStatus

DEFAULT_WEEKS = 12
DEFAULT_DAYS = 7 * DEFAULT_WEEKS
# Sprints averaged for the rolling velocity.
VELOCITY_WINDOW = 3

# Upper bounds in days, None for the open-ended last bucket.
DURATION_BUCKETS = [
    ("< 1 day", 1),
    ("1-2 days", 2),
    ("2-4 days", 4),
    ("4-7 days", 7),
    ("1-2 weeks", 14),
    ("2-4 weeks", 28),
    ("> 4 weeks", None),
]

_REPLAYED_TYPES = (
    TaskEvent.Type.CREATED,
    TaskEvent.Type.MOVED,
    TaskEvent.Type.COMPLETED,
    TaskEvent.Type.DELETED,
    TaskEvent.Type.SPRINT,
    TaskEvent.Type.ESTIMATED,
)


@dataclass(slots=True)
class _Event:
    at: datetime
    key: object
    type: str
    category: str
    value: str


@dataclass(slots=True)
class _TaskState:
    created_at: datetime | None = None
    category: str | None = None
    deleted: bool = False
    sprint: str = ""
    estimate: Decimal | None = None
    # First entry into an active column, kept across a reopen: cycle time
    # runs from the moment work first started.
    active_since: datetime | None = None
    completed_at: datetime | None = None

    @property
    def live(self):
        return self.category is not None and not self.deleted


@dataclass(slots=True)
class DurationSample:
    lead: timedelta
    # None when the task never sat in an active column - it went from the
    # backlog straight to done, so there is no working phase to measure.
    cycle: timedelta | None


def _category_of(event_type, snapshot):
    """Category a status event lands on. The snapshot is authoritative;
    history written before it existed falls back on what the type implies:
    a completion is done, a creation lands in the backlog by default and a
    plain move most often brings a task onto the board."""
    if event_type == TaskEvent.Type.COMPLETED:
        return TaskStatus.Category.DONE
    if snapshot:
        return snapshot
    if event_type == TaskEvent.Type.CREATED:
        return TaskStatus.Category.BACKLOG
    if event_type == TaskEvent.Type.MOVED:
        return TaskStatus.Category.ACTIVE
    return ""


def _parse_estimate(text):
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


class _Replay:
    """The project's event log as a cursor over reconstructed task states.

    ``advance(until)`` applies every event before *until*; passing a
    ``measure`` callback keeps *totals* in step by adding each task's
    measurement after the event and removing the one before, so a sweep
    over many instants costs one pass over the log rather than one pass
    per instant.
    """

    def __init__(self, project):
        self.states = {}
        self.events = []
        self._position = 0
        first_estimate_change = {}
        rows = (
            TaskEvent.objects.filter(project=project, type__in=_REPLAYED_TYPES)
            .order_by("created_at", "uuid")
            .values_list(
                "task_id",
                "task_number",
                "type",
                "to_category",
                "from_value",
                "to_value",
                "created_at",
            )
        )
        for task_id, number, event_type, to_category, from_value, to_value, at in rows:
            key = number if number is not None else task_id
            if key is None:
                continue
            self.events.append(
                _Event(
                    at, key, event_type, _category_of(event_type, to_category), to_value
                )
            )
            if event_type == TaskEvent.Type.ESTIMATED:
                first_estimate_change.setdefault(key, from_value)
        # A task's estimate at creation is not an event. The first change
        # says what it was before; a task never re-estimated still carries
        # it today, unless it has been deleted since - then it is lost.
        self._initial_estimates = {
            number: estimate
            for number, estimate in project.tasks.filter(
                estimate__isnull=False
            ).values_list("number", "estimate")
        }
        for key, before in first_estimate_change.items():
            self._initial_estimates[key] = _parse_estimate(before)

    def advance(self, until, *, measure=None, totals=None):
        while self._position < len(self.events):
            event = self.events[self._position]
            if event.at >= until:
                break
            self._position += 1
            state = self.states.get(event.key)
            if state is None:
                state = _TaskState(estimate=self._initial_estimates.get(event.key))
                self.states[event.key] = state
            before = measure(state) if measure else {}
            _apply(state, event)
            if measure:
                after = measure(state)
                for name in before.keys() | after.keys():
                    totals[name] += after.get(name, 0) - before.get(name, 0)

    def finish(self):
        self.advance(timezone.now() + timedelta(days=1))
        return self.states


def _apply(state, event):
    if event.type == TaskEvent.Type.CREATED:
        state.created_at = event.at
    if event.type in (
        TaskEvent.Type.CREATED,
        TaskEvent.Type.MOVED,
        TaskEvent.Type.COMPLETED,
    ):
        state.category = event.category
        if event.category == TaskStatus.Category.ACTIVE and state.active_since is None:
            state.active_since = event.at
        if event.category == TaskStatus.Category.DONE:
            state.completed_at = event.at
    elif event.type == TaskEvent.Type.DELETED:
        state.deleted = True
    elif event.type == TaskEvent.Type.SPRINT:
        state.sprint = event.value
    elif event.type == TaskEvent.Type.ESTIMATED:
        state.estimate = _parse_estimate(event.value)


def _end_of_day(date):
    """Exclusive upper bound for "everything that happened on *date*"."""
    return timezone.make_aware(datetime.combine(date + timedelta(days=1), time.min))


def task_durations(project, weeks=DEFAULT_WEEKS):
    """One sample per task finished in the last *weeks* weeks.

    Lead time runs from creation to the last completion, cycle time from
    the first entry into an active column to that completion. A task
    reopened since does not count - it is not finished - while a task
    deleted after completion does: the measurement was taken before the
    row went away.
    """
    since = timezone.now() - timedelta(weeks=weeks)
    samples = []
    for state in _Replay(project).finish().values():
        if (
            state.created_at is None
            or state.completed_at is None
            or state.category != TaskStatus.Category.DONE
            or state.completed_at < since
        ):
            continue
        cycle = None
        if state.active_since is not None and state.active_since <= state.completed_at:
            cycle = state.completed_at - state.active_since
        samples.append(
            DurationSample(lead=state.completed_at - state.created_at, cycle=cycle)
        )
    return samples


def duration_summary(samples):
    """Median and 85th percentile in days for both durations, None when
    there is nothing to summarize. The p85 is the "almost always done
    within" figure a team can commit to; the mean would let one stuck task
    drag it around."""
    return {
        "count": len(samples),
        "lead": _percentiles([s.lead for s in samples]),
        "cycle": _percentiles([s.cycle for s in samples if s.cycle is not None]),
    }


def _percentiles(durations):
    if not durations:
        return {"median": None, "p85": None}
    days = sorted(d.total_seconds() / 86400 for d in durations)
    # Nearest-rank: with a handful of samples an interpolated percentile
    # would report a duration no task actually took.
    rank = max(1, -(-len(days) * 85 // 100))
    return {
        "median": round(statistics.median(days), 1),
        "p85": round(days[rank - 1], 1),
    }


def duration_buckets(samples):
    """Histogram of both durations over DURATION_BUCKETS."""
    return {
        "labels": [label for label, _ in DURATION_BUCKETS],
        "lead": _bucket_counts(s.lead for s in samples),
        "cycle": _bucket_counts(s.cycle for s in samples if s.cycle is not None),
    }


def _bucket_counts(durations):
    counts = [0] * len(DURATION_BUCKETS)
    for duration in durations:
        days = duration.total_seconds() / 86400
        for index, (_, upper) in enumerate(DURATION_BUCKETS):
            if upper is None or days < upper:
                counts[index] += 1
                break
    return counts


def cumulative_flow(project, days=DEFAULT_DAYS):
    """Live tasks per status category at the end of each of the last *days*
    days, oldest first; the last point is the current state. Deleted tasks
    leave every band on the day of their deletion."""
    today = timezone.localdate()
    replay = _Replay(project)
    totals = Counter()
    rows = []
    for offset in reversed(range(days)):
        date = today - timedelta(days=offset)
        replay.advance(_end_of_day(date), measure=_live_category, totals=totals)
        rows.append(
            {
                "date": date,
                "backlog": totals[TaskStatus.Category.BACKLOG],
                "active": totals[TaskStatus.Category.ACTIVE],
                "done": totals[TaskStatus.Category.DONE],
            }
        )
    return rows


def _live_category(state):
    return {state.category: 1} if state.live else {}


def sprint_burndown(project, sprint):
    """Effort left in *sprint* at the end of each of its days.

    Effort is the estimate when the project estimates (an unestimated task
    weighs nothing there, and is counted separately so the page can say
    so), one per task otherwise. Membership follows the SPRINT events, so
    a task carried over at sprint close leaves the remaining line at that
    moment, as it should. Days after today have no value yet; a running
    sprint with no end date is drawn up to today. The ideal line falls
    from the scope at the end of the first day to zero on the last.

    Returns None for a sprint that has not started.
    """
    if sprint.start_date is None:
        return None
    today = timezone.localdate()
    end = sprint.end_date if sprint.end_date is not None else today
    end = max(end, sprint.start_date)
    replay = _Replay(project)
    use_estimates = bool(project.estimate_unit)

    def measure(state):
        if not state.live or state.sprint != sprint.name:
            return {}
        effort = (state.estimate or 0) if use_estimates else 1
        measured = {"scope": effort}
        if state.category != TaskStatus.Category.DONE:
            measured["remaining"] = effort
        if use_estimates and state.estimate is None:
            measured["unestimated"] = 1
        return measured

    totals = Counter()
    rows = []
    for offset in range((end - sprint.start_date).days + 1):
        date = sprint.start_date + timedelta(days=offset)
        if date > today:
            rows.append({"date": date, "remaining": None, "scope": None})
            continue
        replay.advance(_end_of_day(date), measure=measure, totals=totals)
        rows.append(
            {"date": date, "remaining": totals["remaining"], "scope": totals["scope"]}
        )
    known = [row for row in rows if row["scope"] is not None]
    initial_scope = known[0]["scope"] if known else 0
    last_day = len(rows) - 1
    for index, row in enumerate(rows):
        row["ideal"] = (
            initial_scope * (last_day - index) / last_day if last_day else initial_scope
        )
    return {
        "days": rows,
        "unit": project.estimate_unit or "tasks",
        "initial_scope": initial_scope,
        "scope": known[-1]["scope"] if known else 0,
        "remaining": known[-1]["remaining"] if known else 0,
        "unestimated": totals["unestimated"],
    }


def sprint_velocity(project, limit=10):
    """Effort completed per closed sprint, oldest first, with the rolling
    average over the last VELOCITY_WINDOW sprints up to and including each.

    Reads the sprints' done tasks as they stand: a closed sprint keeps its
    finished tasks attached precisely so this can be read off the table,
    and a finished task deleted since is gone from the velocity as it is
    gone from the sprint's board.
    """
    sprints = list(
        project.sprints.filter(state=Sprint.State.CLOSED).order_by(
            "end_date", "created_at"
        )
    )[-limit:]
    done = (
        Task.objects.filter(
            sprint__in=sprints, status__category=TaskStatus.Category.DONE
        )
        .values("sprint_id")
        .annotate(points=Sum("estimate"), n=Count("uuid"))
    )
    by_sprint = {row["sprint_id"]: row for row in done}
    rows = []
    for sprint in sprints:
        row = by_sprint.get(sprint.pk)
        if row is None:
            completed = 0
        elif project.estimate_unit:
            completed = row["points"] or 0
        else:
            completed = row["n"]
        rows.append({"sprint": sprint, "completed": completed})
    for index, row in enumerate(rows):
        window = rows[max(0, index - VELOCITY_WINDOW + 1) : index + 1]
        row["average"] = sum(r["completed"] for r in window) / len(window)
    return rows


def velocity_summary(rows):
    """Headline numbers: the last sprint's velocity and the rolling average
    it closed on."""
    if not rows:
        return {"count": 0, "last": None, "average": None}
    return {
        "count": len(rows),
        "last": rows[-1]["completed"],
        "average": rows[-1]["average"],
    }
