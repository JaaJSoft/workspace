from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..models import Sprint, Task, TaskEvent, TaskStatus
from .events import record_task_event
from .members import ProjectRuleError
from .tasks import move_tasks


class SprintStateError(ProjectRuleError):
    """The sprint is not in the state the transition requires."""


class ActiveSprintError(ProjectRuleError):
    """A project runs at most one active sprint at a time."""


class SprintTargetError(ProjectRuleError):
    """The sprint receiving tasks is missing or invalid."""


def active_sprint(project):
    """The project's running sprint, or None."""
    return project.sprints.filter(state=Sprint.State.ACTIVE).first()


def start_sprint(sprint, *, actor=None):
    """Activate a planned sprint and bring its tasks onto the board.

    Guards: only a planned sprint can start, and only while no other sprint
    of the project is active. ``start_date`` defaults to today when unset.
    Sprint tasks still sitting in a backlog column move to the project's
    first active column (the scrum board excludes backlog columns, so they
    would otherwise stay invisible for the whole sprint).
    """
    project = sprint.project
    with transaction.atomic():
        # Locking every sprint row serializes two concurrent starts, the
        # same way delete_status locks all columns.
        sprints = list(project.sprints.select_for_update())
        current = next((s for s in sprints if s.pk == sprint.pk), sprint)
        if current.state != Sprint.State.PLANNED:
            raise SprintStateError("Only a planned sprint can be started.")
        if any(s.state == Sprint.State.ACTIVE for s in sprints):
            raise ActiveSprintError("Another sprint is already active.")
        current.state = Sprint.State.ACTIVE
        if current.start_date is None:
            current.start_date = timezone.localdate()
        current.save(update_fields=["state", "start_date"])
        target = (
            project.statuses.filter(category=TaskStatus.Category.ACTIVE)
            .order_by("position", "created_at")
            .first()
        )
        pending = list(
            current.tasks.filter(
                status__category=TaskStatus.Category.BACKLOG
            ).values_list("uuid", flat=True)
        )
        if target is not None and pending:
            move_tasks(project, target, pending, actor=actor)
    return current


def complete_sprint(sprint, *, move_to=None, actor=None):
    """Close the active sprint, relocating its unfinished tasks.

    ``move_to=None`` returns them to the backlog: the sprint is cleared and
    any task not already in a backlog column moves to the project's first
    backlog column. ``move_to`` set to another non-closed sprint of the
    project carries them over with their board status untouched, so an
    in-progress task resumes where it stopped once that sprint starts.
    Done-column tasks keep the closed sprint - that history is what future
    velocity reports read. ``end_date`` defaults to today when unset.
    """
    project = sprint.project
    with transaction.atomic():
        sprints = list(project.sprints.select_for_update())
        current = next((s for s in sprints if s.pk == sprint.pk), sprint)
        if current.state != Sprint.State.ACTIVE:
            raise SprintStateError("Only the active sprint can be completed.")
        if move_to is not None:
            target = next((s for s in sprints if s.pk == move_to.pk), None)
            if (
                target is None
                or target.pk == current.pk
                or target.state == Sprint.State.CLOSED
            ):
                raise SprintTargetError(
                    "Target sprint must be another open sprint of this project."
                )
            move_to = target
        backlog_status = None
        if move_to is None:
            backlog_status = (
                project.statuses.filter(category=TaskStatus.Category.BACKLOG)
                .order_by("position", "created_at")
                .first()
            )
            if backlog_status is not None:
                # Status row locked before the task rows, matching the lock
                # order of every other column-append writer.
                TaskStatus.objects.select_for_update().get(pk=backlog_status.pk)
        unfinished = sorted(
            # of=("self",): the category exclude joins TaskStatus, and the
            # row lock must not spread to the joined status rows.
            current.tasks.select_for_update(of=("self",)).exclude(
                status__category=TaskStatus.Category.DONE
            ),
            key=lambda t: (t.position, t.created_at),
        )
        now = timezone.now()
        moved = []
        if unfinished:
            fields = ["sprint", "updated_at"]
            for task in unfinished:
                task.sprint = move_to
                # bulk_update bypasses save(), so auto_now would leave
                # updated_at stale; stamp it by hand.
                task.updated_at = now
            if backlog_status is not None:
                to_backlog = [t for t in unfinished if t.status_id != backlog_status.pk]
                last = project.tasks.filter(status=backlog_status).aggregate(
                    last=Max("position")
                )["last"]
                next_position = 0 if last is None else last + 1
                for i, task in enumerate(to_backlog):
                    moved.append((task, task.status))
                    task.status = backlog_status
                    task.position = next_position + i
                fields += ["status", "position"]
            Task.objects.bulk_update(unfinished, fields)
            for task, old_status in moved:
                record_task_event(
                    task,
                    type=TaskEvent.Type.MOVED,
                    actor=actor,
                    from_status=old_status,
                    to_status=backlog_status,
                )
            for task in unfinished:
                record_task_event(
                    task,
                    type=TaskEvent.Type.SPRINT,
                    actor=actor,
                    from_value=current.name,
                    to_value=move_to.name if move_to is not None else "",
                )
        current.state = Sprint.State.CLOSED
        if current.end_date is None:
            current.end_date = timezone.localdate()
        current.save(update_fields=["state", "end_date"])
    return current


def assign_tasks_to_sprint(project, sprint, task_uuids, *, actor=None):
    """Assign the listed tasks to *sprint*; None returns them to the pool.

    Backlog planning: tasks already on the target sprint and unknown UUIDs
    are skipped. Assigning into a planned sprint never touches the board
    status; assigning into the *running* sprint also moves backlog-column
    tasks to the first active column, mirroring start_sprint - a task of
    the running sprint left in a backlog column would be invisible on the
    sprint board. One SPRINT event per changed task with the sprint
    *names* snapshotted - sprints are renamable and deletable, a FK would
    rewrite history. Returns the changed tasks.
    """
    if sprint is not None and sprint.state == Sprint.State.CLOSED:
        raise SprintTargetError("Tasks cannot be assigned to a closed sprint.")
    target_id = sprint.pk if sprint is not None else None
    with transaction.atomic():
        board_status = None
        if sprint is not None and sprint.state == Sprint.State.ACTIVE:
            board_status = (
                project.statuses.filter(category=TaskStatus.Category.ACTIVE)
                .order_by("position", "created_at")
                .first()
            )
            if board_status is not None:
                # Status row locked before the task rows, matching the lock
                # order of every other column-append writer.
                TaskStatus.objects.select_for_update().get(pk=board_status.pk)
        tasks = list(
            # of=("self",): select_related joins sprint and status rows,
            # and the row lock must not spread to them.
            project.tasks.select_for_update(of=("self",))
            .select_related("sprint", "status")
            .filter(uuid__in=task_uuids)
        )
        now = timezone.now()
        changed = []
        for task in tasks:
            if task.sprint_id == target_id:
                continue
            changed.append((task, task.sprint))
            task.sprint = sprint
            # bulk_update bypasses save(), so auto_now would leave
            # updated_at stale; stamp it by hand.
            task.updated_at = now
        if changed:
            Task.objects.bulk_update(
                [task for task, _ in changed], ["sprint", "updated_at"]
            )
        for task, old_sprint in changed:
            record_task_event(
                task,
                type=TaskEvent.Type.SPRINT,
                actor=actor,
                from_value=old_sprint.name if old_sprint is not None else "",
                to_value=sprint.name if sprint is not None else "",
            )
        if board_status is not None:
            # Every listed task is on the running sprint by now (changed or
            # already there); pull the ones still in a backlog column onto
            # the board.
            to_board = [
                task.uuid
                for task in tasks
                if task.status.category == TaskStatus.Category.BACKLOG
            ]
            if to_board:
                move_tasks(project, board_status, to_board, actor=actor)
    return [task for task, _ in changed]


def propagate_sprint_rename(project, old_name, new_name):
    """Carry a sprint's SPRINT event snapshots over to its new name.

    Events snapshot the sprint *name* so the trail survives the sprint's
    deletion. A rename must follow, or the burndown loses every task that
    joined under the old name and the activity feed keeps naming a sprint
    the board no longer shows.
    """
    events = project.task_events.filter(type=TaskEvent.Type.SPRINT)
    events.filter(from_value=old_name).update(from_value=new_name)
    events.filter(to_value=old_name).update(to_value=new_name)
