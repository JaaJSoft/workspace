from django.contrib.auth.decorators import login_required
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.uuids import parse_uuid_or_none
from workspace.projects.models import Project, TaskStatus
from workspace.projects.queries import get_project_role, user_project_ids
from workspace.projects.services.projects import get_or_create_personal_project
from workspace.users.services.settings import get_setting, set_setting

VIEW_BOARD = "board"
VIEW_BACKLOG = "backlog"


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
def project_root(request, project_uuid):
    """Land on the project's last-opened view.

    Reserved for a future project overview page; until then it only
    dispatches. Access is checked by the target view, not here.
    """
    last_view = get_setting(
        request.user, "projects", f"last_view:{project_uuid}", default=VIEW_BOARD
    )
    if last_view not in (VIEW_BOARD, VIEW_BACKLOG):
        last_view = VIEW_BOARD
    return redirect(f"projects_ui:{last_view}", project_uuid=project_uuid)


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


def _base_context(request, project, role, view):
    statuses = list(project.statuses.order_by("position", "created_at"))
    members = project.members.filter(left_at__isnull=True).select_related("user")
    context = {
        "project": project,
        "role": role,
        "view": view,
        "writable": not project.is_archived,
        "backlog_count": project.tasks.filter(
            status__category=TaskStatus.Category.BACKLOG
        ).count(),
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
    return context


def _record_visit(user, project_uuid, view):
    set_setting(user, "projects", "last_project", str(project_uuid))
    set_setting(user, "projects", f"last_view:{project_uuid}", view)


def _render_project_view(request, context):
    if request.headers.get("X-Alpine-Request"):
        return render(request, "projects/ui/partials/_content.html", context)
    return render(request, "projects/ui/project.html", context)


@login_required
@ensure_csrf_cookie
def board(request, project_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    _record_visit(request.user, project_uuid, VIEW_BOARD)
    context = _base_context(request, project, role, VIEW_BOARD)
    tasks = list(
        project.tasks.exclude(status__category=TaskStatus.Category.BACKLOG)
        .select_related("status")
        .prefetch_related("assignees", "labels")
        .order_by("position", "created_at")
    )
    context["columns"] = [
        {"status": s, "tasks": [t for t in tasks if t.status_id == s.pk]}
        for s in context["statuses"]
        if s.category != TaskStatus.Category.BACKLOG
    ]
    return _render_project_view(request, context)


@login_required
@ensure_csrf_cookie
def backlog(request, project_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    _record_visit(request.user, project_uuid, VIEW_BACKLOG)
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
    return _render_project_view(request, context)
