from collections import defaultdict
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.charts import column_chart
from workspace.common.uuids import parse_uuid_or_none
from workspace.core.services.activity import annotate_time_ago
from workspace.projects.actions import ProjectActionRegistry
from workspace.projects.models import Project, ProjectMember, TaskStatus
from workspace.projects.queries import (
    get_project_role,
    project_users,
    user_project_ids,
)
from workspace.projects.services.analytics import (
    flow_summary,
    open_task_distribution,
    weekly_flow,
)
from workspace.projects.services.events import events_for_project, serialize_task_event
from workspace.projects.services.projects import get_or_create_personal_project
from workspace.projects.services.references import REFERENCE_RE
from workspace.projects.services.rendering import render_task_description
from workspace.users.services.settings import get_setting, set_setting

VIEW_OVERVIEW = "overview"
VIEW_BOARD = "board"
VIEW_BACKLOG = "backlog"
VIEW_TASKS = "tasks"
VIEW_ANALYTICS = "analytics"
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
    """Panel context for a valid ?task= deep link (UUID or WR-42), else empty."""
    raw = (request.GET.get("task") or "").strip()
    if not raw:
        return {}
    qs = project.tasks.select_related("status", "created_by").prefetch_related(
        "assignees", "labels"
    )
    task_uuid = parse_uuid_or_none(raw)
    if task_uuid is not None:
        task = qs.filter(uuid=task_uuid).first()
    else:
        task = _task_by_reference(qs, project, raw)
    if task is None:
        return {}
    return _task_panel_context(request.user, project, role, task)


def _task_by_reference(qs, project, raw):
    """Resolve WR-42 within *project*. A key mismatch or unknown number
    resolves to nothing, mirroring the unknown-UUID behavior so existence
    is never leaked."""
    match = REFERENCE_RE.match(raw)
    if match is None or match.group(1).upper() != project.key:
        return None
    return qs.filter(number=int(match.group(2))).first()


def _base_context(request, project, role, view):
    statuses = list(project.statuses.order_by("position", "created_at"))
    members = project.members.filter(left_at__isnull=True).select_related("user")
    context = {
        "project": project,
        "role": role,
        "view": view,
        "writable": not project.is_archived,
        "today": timezone.localdate(),
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
            {
                "id": str(u.pk),
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
            }
            for u in project_users(project)
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
        serialize_task_event(ev)
        for ev in task.events.select_related("actor", "project")[:20]
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
        "panel_can_comment": "comment" in action_ids,
        "panel_description_html": render_task_description(task.description),
        "panel_task_data": {
            "uuid": str(task.uuid),
            "title": task.title,
            "description": task.description,
            "status": str(task.status_id),
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else "",
            "assignees": [str(u.pk) for u in task.assignees.all()],
            "assignee_users": [
                {"id": str(u.pk), "username": u.username} for u in task.assignees.all()
            ],
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
        "labels_data": [
            {"uuid": str(label.uuid), "name": label.name, "color": label.color}
            for label in project.labels.all()
        ],
    }
    context.update(_task_panel_context(request.user, project, role, task))
    return render(request, "projects/ui/partials/task_panel.html", context)


@login_required
def task_card(request, project_uuid, task_uuid):
    """Compact task card for the hover popover (calendar overlay, and any
    other surface that shows a task it cannot render in full)."""
    project, _role = _get_project_or_404(request.user, project_uuid)
    task = get_object_or_404(
        project.tasks.select_related("status").prefetch_related("assignees", "labels"),
        uuid=task_uuid,
    )
    return render(
        request,
        "projects/ui/partials/_task_popover_card.html",
        {
            "project": project,
            "task": task,
            "assignees": list(task.assignees.all())[:5],
            "today": timezone.localdate(),
        },
    )


@login_required
@ensure_csrf_cookie
def board(request, project_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    _record_visit(request.user, project_uuid)
    context = _base_context(request, project, role, VIEW_BOARD)
    context["backlog_count"] = project.tasks.filter(
        status__category=TaskStatus.Category.BACKLOG
    ).count()
    tasks_qs = (
        project.tasks.exclude(status__category=TaskStatus.Category.BACKLOG)
        .select_related("status")
        .prefetch_related("assignees", "labels")
        .order_by("position", "created_at")
    )
    hidden_counts = {}
    if project.done_retention_days is not None:
        cutoff = timezone.now() - timedelta(days=project.done_retention_days)
        # completed_at__lt is null-safe: a done task missing its timestamp
        # never matches and stays visible.
        expired = Q(status__category=TaskStatus.Category.DONE, completed_at__lt=cutoff)
        hidden_counts = dict(
            project.tasks.filter(expired)
            .values_list("status_id")
            .annotate(n=Count("uuid"))
            .values_list("status_id", "n")
        )
        tasks_qs = tasks_qs.exclude(expired)
    tasks_by_status = defaultdict(list)
    for task in tasks_qs:
        tasks_by_status[task.status_id].append(task)
    context["columns"] = [
        {
            "status": s,
            "tasks": tasks_by_status[s.pk],
            "hidden_count": hidden_counts.get(s.pk, 0),
        }
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
def all_tasks(request, project_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    _record_visit(request.user, project_uuid)
    context = _base_context(request, project, role, VIEW_TASKS)
    context["backlog_count"] = project.tasks.filter(
        status__category=TaskStatus.Category.BACKLOG
    ).count()
    context["all_tasks"] = list(
        project.tasks.select_related("status")
        .prefetch_related("assignees", "labels")
        .order_by("status__position", "status__created_at", "position", "created_at")
    )
    return _render_project_view(request, context)


@login_required
@ensure_csrf_cookie
def analytics(request, project_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    _record_visit(request.user, project_uuid)
    context = _base_context(request, project, role, VIEW_ANALYTICS)
    context["backlog_count"] = project.tasks.filter(
        status__category=TaskStatus.Category.BACKLOG
    ).count()
    flow = weekly_flow(project)
    summary = flow_summary(flow)
    distribution = {
        key: _with_bar_maximum(entries)
        for key, entries in open_task_distribution(project).items()
    }
    open_count = sum(entry["count"] for entry in distribution["by_priority"])
    context.update(
        {
            "flow_summary": summary,
            # "%b %d" rather than "%b %-d": the padding-strip flag is not
            # portable to Windows.
            "flow_chart": column_chart(
                [row["week_start"].strftime("%b %d") for row in flow],
                [
                    {
                        "name": "Created",
                        "css_class": "fill-accent",
                        "values": [row["created"] for row in flow],
                    },
                    {
                        "name": "Completed",
                        "css_class": "fill-success",
                        "values": [row["completed"] for row in flow],
                    },
                ],
            ),
            "distribution": distribution,
            "open_count": open_count,
            "is_empty": open_count == 0
            and summary["created"] == 0
            and summary["completed"] == 0,
        }
    )
    return _render_project_view(request, context)


def _with_bar_maximum(entries):
    """Bar widths are relative to the busiest row, and {% widthratio %}
    cannot compute that itself. Floors at 1 so an all-zero breakdown
    divides safely."""
    top = max((entry["count"] for entry in entries), default=0)
    return [{**entry, "max_count": top or 1} for entry in entries]


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
        "key": project.key,
        "done_retention_days": project.done_retention_days,
        "groups": [
            {"id": group.pk, "name": group.name}
            for group in project.groups.order_by("name")
        ],
        "type": project.type,
        "archived": project.is_archived,
    }
    return _render_project_view(request, context)
