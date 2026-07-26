from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.uuids import parse_uuid_or_none
from workspace.core.services.activity import annotate_time_ago
from workspace.projects.actions import ProjectActionRegistry
from workspace.projects.models import Project, ProjectMember, TaskStatus
from workspace.projects.queries import get_project_role, user_project_ids
from workspace.projects.services.events import events_for_project, serialize_task_event
from workspace.projects.services.projects import get_or_create_personal_project
from workspace.projects.services.rendering import render_task_description
from workspace.users.services.settings import get_setting, set_setting

VIEW_OVERVIEW = "overview"
VIEW_BOARD = "board"
VIEW_BACKLOG = "backlog"
VIEW_SETTINGS = "settings"


@login_required
def index(request):
    """Land on the last-opened project, falling back to the personal one."""
    last = parse_uuid_or_none(
        get_setting(request.user, "projects", "last_project", default="") or ""
    )
    if last is not None and last in user_project_ids(request.user):
        return redirect("projects_ui:project", project_uuid=last)
    project = get_or_create_personal_project(request.user)
    return redirect("projects_ui:project", project_uuid=project.uuid)


@login_required
@ensure_csrf_cookie
def overview(request, project_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    _record_visit(request.user, project_uuid)
    context = _base_context(request, project, role, VIEW_OVERVIEW)
    counts = project.tasks.aggregate(
        board_count=Count(
            "uuid", filter=Q(status__category=TaskStatus.Category.ACTIVE)
        ),
        backlog_count=Count(
            "uuid", filter=Q(status__category=TaskStatus.Category.BACKLOG)
        ),
        done_count=Count("uuid", filter=Q(status__category=TaskStatus.Category.DONE)),
    )
    context.update(counts)
    context["recent_events"] = events_for_project(project)
    return _render_project_view(request, context)


def _get_project_or_404(user, project_uuid):
    project = get_object_or_404(Project, uuid=project_uuid)
    role = get_project_role(user, project)
    if role is None:
        raise Http404
    return project, role


def _sidebar_projects(user):
    return (
        Project.objects.filter(uuid__in=user_project_ids(user))
        .annotate(
            open_task_count=Count(
                "tasks",
                filter=~Q(tasks__status__category=TaskStatus.Category.DONE),
            )
        )
        .order_by(
            Case(
                When(type=Project.Type.PERSONAL, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            "name",
        )
    )


def _deep_link_panel(request, project, role):
    """Panel context for a valid ?task= deep link, else empty."""
    task_uuid = parse_uuid_or_none(request.GET.get("task") or "")
    if task_uuid is None:
        return {}
    task = (
        project.tasks.select_related("status", "created_by")
        .prefetch_related("assignees", "labels")
        .filter(uuid=task_uuid)
        .first()
    )
    if task is None:
        return {}
    return _task_panel_context(request.user, project, role, task)


def _base_context(request, project, role, view):
    statuses = list(project.statuses.order_by("position", "created_at"))
    members = project.members.filter(left_at__isnull=True).select_related("user")
    context = {
        "project": project,
        "role": role,
        "view": view,
        "writable": not project.is_archived,
        "statuses": statuses,
        "members": members,
        "statuses_data": [
            {"uuid": str(s.uuid), "name": s.name, "category": s.category}
            for s in statuses
        ],
        "labels_data": [
            {"uuid": str(label.uuid), "name": label.name, "color": label.color}
            for label in project.labels.all()
        ],
        "members_data": [
            {"id": str(m.user_id), "username": m.user.username} for m in members
        ],
    }
    if not request.headers.get("X-Alpine-Request"):
        context["projects"] = _sidebar_projects(request.user)
        context.update(_deep_link_panel(request, project, role))
    return context


def _record_visit(user, project_uuid):
    set_setting(user, "projects", "last_project", str(project_uuid))


def _render_project_view(request, context):
    if request.headers.get("X-Alpine-Request"):
        return render(request, "projects/ui/partials/_content.html", context)
    return render(request, "projects/ui/project.html", context)


def _task_panel_context(user, project, role, task):
    events = [
        serialize_task_event(ev) for ev in task.events.select_related("actor")[:20]
    ]
    for event in events:
        # Same color as the registered projects activity provider.
        event["source_color"] = "accent"
    annotate_time_ago(events)
    action_ids = [
        action["id"]
        for action in ProjectActionRegistry.get_available_actions(
            user, task, role=role, archived=project.is_archived
        )
    ]
    return {
        "panel_task": task,
        "panel_events": events,
        "panel_action_ids": action_ids,
        "panel_description_html": render_task_description(task.description),
        "panel_task_data": {
            "uuid": str(task.uuid),
            "title": task.title,
            "description": task.description,
            "status": str(task.status_id),
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else "",
            "assignees": [str(u.pk) for u in task.assignees.all()],
            "labels": [str(label.uuid) for label in task.labels.all()],
        },
    }


@login_required
def task_panel(request, project_uuid, task_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    task = get_object_or_404(
        project.tasks.select_related("status", "created_by").prefetch_related(
            "assignees", "labels"
        ),
        uuid=task_uuid,
    )
    context = {
        "project": project,
        "role": role,
        "writable": not project.is_archived,
        "statuses": list(project.statuses.order_by("position", "created_at")),
        "members": project.members.filter(left_at__isnull=True).select_related("user"),
        "labels_data": [
            {"uuid": str(label.uuid), "name": label.name, "color": label.color}
            for label in project.labels.all()
        ],
    }
    context.update(_task_panel_context(request.user, project, role, task))
    return render(request, "projects/ui/partials/task_panel.html", context)


@login_required
@ensure_csrf_cookie
def board(request, project_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    _record_visit(request.user, project_uuid)
    context = _base_context(request, project, role, VIEW_BOARD)
    context["backlog_count"] = project.tasks.filter(
        status__category=TaskStatus.Category.BACKLOG
    ).count()
    tasks = list(
        project.tasks.exclude(status__category=TaskStatus.Category.BACKLOG)
        .select_related("status")
        .prefetch_related("assignees", "labels")
        .order_by("position", "created_at")
    )
    tasks_by_status = defaultdict(list)
    for task in tasks:
        tasks_by_status[task.status_id].append(task)
    context["columns"] = [
        {"status": s, "tasks": tasks_by_status[s.pk]}
        for s in context["statuses"]
        if s.category != TaskStatus.Category.BACKLOG
    ]
    return _render_project_view(request, context)


@login_required
@ensure_csrf_cookie
def backlog(request, project_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    _record_visit(request.user, project_uuid)
    context = _base_context(request, project, role, VIEW_BACKLOG)
    backlog_statuses = [
        s for s in context["statuses"] if s.category == TaskStatus.Category.BACKLOG
    ]
    context["backlog_status"] = backlog_statuses[0] if backlog_statuses else None
    context["backlog_tasks"] = list(
        project.tasks.filter(status__category=TaskStatus.Category.BACKLOG)
        .select_related("status")
        .prefetch_related("assignees", "labels")
        .order_by("position", "created_at")
    )
    context["backlog_count"] = len(context["backlog_tasks"])
    return _render_project_view(request, context)


@login_required
@ensure_csrf_cookie
def settings_view(request, project_uuid):
    """Admin-only settings; 404 for everyone else so nothing leaks."""
    project, role = _get_project_or_404(request.user, project_uuid)
    if role != ProjectMember.Role.ADMIN:
        raise Http404
    _record_visit(request.user, project_uuid)
    context = _base_context(request, project, role, VIEW_SETTINGS)
    counts = dict(
        project.tasks.values_list("status_id")
        .annotate(n=Count("uuid"))
        .values_list("status_id", "n")
    )
    context["columns_data"] = [
        {
            "uuid": str(s.uuid),
            "name": s.name,
            "category": s.category,
            "color": s.color,
            "task_count": counts.get(s.pk, 0),
        }
        for s in context["statuses"]
    ]
    context["project_data"] = {
        "name": project.name,
        "description": project.description,
        "group": project.group_id,
        "type": project.type,
        "archived": project.is_archived,
    }
    return _render_project_view(request, context)
