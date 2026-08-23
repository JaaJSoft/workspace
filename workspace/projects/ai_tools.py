"""AI tools for the Projects module."""

import json
import logging
import uuid
from datetime import date, timedelta

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


class ListMyTasksParams(BaseModel):
    project: str = Field(
        default="",
        description="Only tasks of this project (name or key, e.g. 'Website' "
        "or 'WEB'). Empty means every project.",
    )
    due_within_days: int = Field(
        default=0,
        description="Only tasks due within this many days, overdue included. "
        "0 means no due-date filter.",
    )
    limit: int = Field(
        default=30, description="Maximum number of tasks to return (default 30)."
    )


class SearchTasksParams(BaseModel):
    query: str = Field(
        description="The search term to look for in task titles and "
        "descriptions, or an exact task reference like WR-42."
    )
    project: str = Field(
        default="",
        description="Only tasks of this project (name or key). Optional.",
    )
    assignee: str = Field(
        default="",
        description="Only tasks assigned to this exact username. Optional.",
    )
    status: str = Field(
        default="",
        description="Only tasks in this status (column name). Optional.",
    )
    due_before: str = Field(
        default="",
        description="Only tasks due on or before this date (YYYY-MM-DD). Optional.",
    )


class CreateTaskParams(BaseModel):
    title: str = Field(max_length=255, description="The task title.")
    project: str = Field(
        default="",
        description="Project to create the task in (name or key). If omitted, "
        "the task goes to the user's personal project.",
    )
    description: str = Field(default="", description="Optional task description.")
    priority: str = Field(
        default="",
        description="Task priority: low, medium, high or urgent. Defaults to medium.",
    )
    due_date: str = Field(default="", description="Optional due date (YYYY-MM-DD).")
    assignee: str = Field(
        default="",
        description="Exact username of a project member to assign. Optional; "
        "use search_users first when the user names a person informally.",
    )


class MoveTaskParams(BaseModel):
    task_uuid: uuid.UUID = Field(
        description="UUID of the task, as returned by list_my_tasks, "
        "search_tasks or create_task."
    )
    status: str = Field(
        description="Name of the target status (board column). Statuses are "
        "per-project: take the name from list_projects, never guess it."
    )


class UpdateTaskParams(BaseModel):
    task_uuid: uuid.UUID = Field(
        description="UUID of the task, as returned by list_my_tasks, "
        "search_tasks or create_task."
    )
    assignee: str = Field(
        default="",
        description="Exact username of a project member to assign to the task. "
        "Optional; existing assignees are kept.",
    )
    due_date: str = Field(
        default="",
        description="New due date (YYYY-MM-DD), or 'none' to clear it. "
        "Optional; empty means unchanged.",
    )


class CommentOnTaskParams(BaseModel):
    task_uuid: uuid.UUID = Field(
        description="UUID of the task, as returned by list_my_tasks, "
        "search_tasks or create_task."
    )
    body: str = Field(description="The comment text (plain text or markdown).")


def _resolve_project(user, name):
    """Accessible, non-archived project matching *name* by key or name.

    Returns ``(project, error)`` with exactly one side set.
    """
    from .models import Project
    from .queries import user_project_ids

    accessible = Project.objects.filter(
        uuid__in=user_project_ids(user), archived_at__isnull=True
    )
    wanted = name.strip()
    project = (
        accessible.filter(key__iexact=wanted).first()
        or accessible.filter(name__iexact=wanted).first()
    )
    if project is None:
        names = ", ".join(p.name for p in accessible) or "(none)"
        return None, f'Error: no project named "{wanted}". Your projects: {names}'
    return project, None


def _get_task(user, task_uuid):
    """Task visible to *user*, or None (unknown and inaccessible look alike)."""
    from .models import Task
    from .queries import user_project_ids

    return (
        Task.objects.filter(uuid=task_uuid, project_id__in=user_project_ids(user))
        .select_related("project", "status")
        .first()
    )


def _resolve_member(project, username):
    """Project member matching *username*, or ``(None, error)``."""
    from .queries import project_users

    wanted = username.strip()
    member = next(
        (u for u in project_users(project) if u.username.lower() == wanted.lower()),
        None,
    )
    if member is None:
        return None, (
            f'Error: "{wanted}" is not a member of project "{project.name}". '
            "Use search_users to find the exact username."
        )
    return member, None


def _parse_date(value, field):
    try:
        return date.fromisoformat(value.strip()), None
    except ValueError:
        return None, (
            f'Error: could not parse {field} "{value}". Use YYYY-MM-DD format.'
        )


def _task_entry(task):
    return {
        "uuid": str(task.uuid),
        "reference": task.reference,
        "title": task.title,
        "project": task.project.name,
        "status": task.status.name,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else "",
        "assignees": [u.username for u in task.assignees.all()],
    }


class ProjectsToolProvider(ToolProvider):
    @tool(
        badge_icon="📋",
        badge_label="Listed projects",
        badge_running_label="Listing projects",
    )
    def list_projects(self, args, user, bot, conversation_id, context):
        """List the user's projects with their board statuses (column names). \
Call this before create_task, move_task or search_tasks when you need a real \
project name or status name — statuses are per-project, never guess them."""
        from .models import Project
        from .queries import user_project_ids

        projects = Project.objects.filter(
            uuid__in=user_project_ids(user), archived_at__isnull=True
        ).prefetch_related("statuses")
        results = [
            {
                "name": p.name,
                "key": p.key,
                "type": p.type,
                "statuses": [
                    s.name
                    for s in sorted(
                        p.statuses.all(), key=lambda s: (s.position, s.created_at)
                    )
                ],
            }
            for p in projects
        ]
        if not results:
            return "You have no projects yet."
        return json.dumps(results, ensure_ascii=False)

    @tool(
        badge_icon="✅",
        badge_label="Checked tasks",
        badge_running_label="Checking tasks",
        params=ListMyTasksParams,
    )
    def list_my_tasks(self, args, user, bot, conversation_id, context):
        """List the open tasks assigned to the user, most urgent first \
(overdue first, undated last). Call this when the user asks what is on their \
plate, what they should work on, or about their deadlines. Optionally filter \
by project or due window. For tasks assigned to other people use search_tasks."""
        from .queries import assigned_open_tasks

        qs = assigned_open_tasks(user)
        if args.project.strip():
            project, error = _resolve_project(user, args.project)
            if error:
                return error
            qs = qs.filter(project=project)
        if args.due_within_days > 0:
            from django.utils import timezone

            cutoff = timezone.localdate() + timedelta(days=args.due_within_days)
            qs = qs.filter(due_date__lte=cutoff)
        limit = max(1, min(args.limit, 50))
        tasks = qs.prefetch_related("assignees")[:limit]
        results = [_task_entry(t) for t in tasks]
        if not results:
            return "No open tasks assigned to you match these filters."
        return json.dumps(results, ensure_ascii=False)

    @tool(
        badge_icon="🔍",
        badge_label="Searched tasks",
        badge_running_label="Searching tasks",
        detail_key="query",
        params=SearchTasksParams,
    )
    def search_tasks(self, args, user, bot, conversation_id, context):
        """Search tasks by keyword or by reference (e.g. WR-42) across every \
project the user can access — not just their own tasks. Returns up to 20 \
matches with reference, title, project, status, priority, due date and \
assignees. Call this when the user asks about a task by topic or reference, \
or wants an overview like the overdue tasks of a project."""
        from django.db.models import Q, prefetch_related_objects

        from .services.search import combined_task_search

        query = args.query.strip()
        if not query:
            return "Error: query is required"

        extra = Q()
        if args.project.strip():
            project, error = _resolve_project(user, args.project)
            if error:
                return error
            extra &= Q(project=project)
        if args.assignee.strip():
            extra &= Q(assignees__username__iexact=args.assignee.strip())
        if args.status.strip():
            extra &= Q(status__name__iexact=args.status.strip())
        if args.due_before.strip():
            due_before, error = _parse_date(args.due_before, "due_before")
            if error:
                return error
            extra &= Q(due_date__lte=due_before)

        tasks, _ = combined_task_search(user, query, limit=20, extra_filter=extra)
        prefetch_related_objects(tasks, "assignees")
        results = [_task_entry(t) for t in tasks]
        if not results:
            return f'No tasks found matching "{query}".'
        return json.dumps(results, ensure_ascii=False)

    @tool(
        badge_icon="➕",
        badge_label="Created task",
        badge_running_label="Creating task",
        detail_key="title",
        params=CreateTaskParams,
    )
    def create_task(self, args, user, bot, conversation_id, context):
        """Create a task on one of the user's project boards. Call this when \
the user asks to add, create, or note down a task or todo. Without a project \
the task goes to their personal project; when the user names a project, call \
list_projects first if unsure of its exact name. New tasks land in the \
project's backlog column."""
        from .models import Task
        from .services import tasks as task_service
        from .services.projects import get_or_create_personal_project

        title = args.title.strip()
        if not title:
            return "Error: title is required"

        if args.project.strip():
            project, error = _resolve_project(user, args.project)
            if error:
                return error
        else:
            project = get_or_create_personal_project(user)

        priority = args.priority.strip().lower() or Task.Priority.MEDIUM
        if priority not in Task.Priority.values:
            choices = ", ".join(Task.Priority.values)
            return f'Error: invalid priority "{args.priority}". Use one of: {choices}'

        due_date = None
        if args.due_date.strip():
            due_date, error = _parse_date(args.due_date, "due_date")
            if error:
                return error

        assignees = ()
        if args.assignee.strip():
            member, error = _resolve_member(project, args.assignee)
            if error:
                return error
            assignees = (member,)

        task = task_service.create_task(
            project,
            user,
            title=title,
            description=args.description.strip(),
            priority=priority,
            due_date=due_date,
            assignees=assignees,
        )
        logger.info(
            "AI created task %s in project %s for %s",
            scrub(task.reference),
            scrub(project.name),
            scrub(user.username),
        )
        assigned = f", assigned to {assignees[0].username}" if assignees else ""
        return (
            f'Created task {task.reference} "{title}" in project "{project.name}" '
            f"(status: {task.status.name}{assigned}, id: {task.uuid})."
        )

    @tool(
        badge_icon="🔀",
        badge_label="Moved task",
        badge_running_label="Moving task",
        detail_key="status",
        params=MoveTaskParams,
    )
    def move_task(self, args, user, bot, conversation_id, context):
        """Move a task to another status (board column) of its project, e.g. \
mark it done or start working on it. Statuses are per-project: use the names \
returned by list_projects for the task's project. Find the task's UUID with \
list_my_tasks or search_tasks first."""
        from .services.tasks import apply_status_change

        task = _get_task(user, args.task_uuid)
        if task is None:
            return "Error: task not found."
        if task.project.is_archived:
            return f'Error: project "{task.project.name}" is archived.'

        wanted = args.status.strip()
        target = next(
            (
                s
                for s in task.project.statuses.all()
                if s.name.lower() == wanted.lower()
            ),
            None,
        )
        if target is None:
            names = ", ".join(
                s.name for s in task.project.statuses.order_by("position", "created_at")
            )
            return (
                f'Error: project "{task.project.name}" has no status "{wanted}". '
                f"Its statuses: {names}"
            )
        if target.pk == task.status_id:
            return f'Task {task.reference} is already in "{target.name}".'

        old_status = task.status
        task.status = target
        apply_status_change(task, actor=user, old_status=old_status)
        logger.info(
            "AI moved task %s to %s for %s",
            scrub(task.reference),
            scrub(target.name),
            scrub(user.username),
        )
        return (
            f'Moved task {task.reference} "{task.title}" from '
            f'"{old_status.name}" to "{target.name}".'
        )

    @tool(
        badge_icon="✏️",
        badge_label="Updated task",
        badge_running_label="Updating task",
        params=UpdateTaskParams,
    )
    def update_task(self, args, user, bot, conversation_id, context):
        """Assign a task to a project member and/or change its due date. Call \
this to reassign work or push a deadline; to change a task's status use \
move_task instead. Find the task's UUID with list_my_tasks or search_tasks \
first."""
        from .models import TaskEvent
        from .services.assignments import notify_assigned
        from .services.events import record_task_event
        from .services.watchers import auto_watch

        task = _get_task(user, args.task_uuid)
        if task is None:
            return "Error: task not found."
        if task.project.is_archived:
            return f'Error: project "{task.project.name}" is archived.'

        assignee = args.assignee.strip()
        raw_due = args.due_date.strip()
        if not assignee and not raw_due:
            return "Error: nothing to update — pass an assignee and/or a due_date."

        changes = []
        if raw_due:
            if raw_due.lower() == "none":
                due_date = None
            else:
                due_date, error = _parse_date(raw_due, "due_date")
                if error:
                    return error
            if task.due_date != due_date:
                task.due_date = due_date
                task.save(update_fields=["due_date", "updated_at"])
                record_task_event(task, type=TaskEvent.Type.UPDATED, actor=user)
            changes.append(
                f"due date set to {due_date.isoformat()}"
                if due_date
                else "due date cleared"
            )

        if assignee:
            member, error = _resolve_member(task.project, assignee)
            if error:
                return error
            if task.assignees.filter(pk=member.pk).exists():
                changes.append(f"{member.username} was already assigned")
            else:
                task.assignees.add(member)
                record_task_event(task, type=TaskEvent.Type.ASSIGNED, actor=user)
                auto_watch(task, [member])
                notify_assigned(task, user, [member])
                changes.append(f"assigned to {member.username}")

        logger.info(
            "AI updated task %s for %s", scrub(task.reference), scrub(user.username)
        )
        return f'Updated task {task.reference} "{task.title}": {"; ".join(changes)}.'

    @tool(
        badge_icon="💬",
        badge_label="Commented on task",
        badge_running_label="Commenting on task",
        params=CommentOnTaskParams,
    )
    def comment_on_task(self, args, user, bot, conversation_id, context):
        """Post a comment on a task, in the user's name. The task's watchers, \
assignees and prior commenters are notified; mention a project member with \
@username to ping them directly. Find the task's UUID with list_my_tasks or \
search_tasks first."""
        from .services.comments import add_comment

        task = _get_task(user, args.task_uuid)
        if task is None:
            return "Error: task not found."
        if task.project.is_archived:
            return f'Error: project "{task.project.name}" is archived.'

        body = args.body.strip()
        if not body:
            return "Error: body is required"

        add_comment(task, user, body)
        logger.info(
            "AI commented on task %s for %s",
            scrub(task.reference),
            scrub(user.username),
        )
        return f'Commented on task {task.reference} "{task.title}".'
