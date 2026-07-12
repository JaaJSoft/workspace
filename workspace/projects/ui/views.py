from django.contrib.auth.decorators import login_required
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.projects.models import Project, TaskStatus
from workspace.projects.queries import get_project_role, user_project_ids
from workspace.projects.services.projects import get_or_create_personal_project


@login_required
@ensure_csrf_cookie
def index(request):
    get_or_create_personal_project(request.user)
    projects = (
        Project.objects.filter(uuid__in=user_project_ids(request.user))
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
    context = {"projects": projects}
    if request.headers.get("X-Alpine-Request"):
        return render(request, "projects/ui/partials/project_list.html", context)
    return render(request, "projects/ui/index.html", context)


@login_required
@ensure_csrf_cookie
def project_view(request, project_uuid):
    project = get_object_or_404(Project, uuid=project_uuid)
    role = get_project_role(request.user, project)
    if role is None:
        raise Http404
    statuses = list(project.statuses.order_by("position", "created_at"))
    tasks = list(
        project.tasks.select_related("status")
        .prefetch_related("assignees", "labels")
        .order_by("position", "created_at")
    )
    backlog_statuses = [
        s for s in statuses if s.category == TaskStatus.Category.BACKLOG
    ]
    board_statuses = [s for s in statuses if s.category != TaskStatus.Category.BACKLOG]
    context = {
        "project": project,
        "role": role,
        "writable": not project.is_archived,
        "columns": [
            {"status": s, "tasks": [t for t in tasks if t.status_id == s.pk]}
            for s in board_statuses
        ],
        "backlog_status": backlog_statuses[0] if backlog_statuses else None,
        "backlog_tasks": [
            t for t in tasks if t.status.category == TaskStatus.Category.BACKLOG
        ],
        "members": project.members.filter(left_at__isnull=True).select_related("user"),
        "statuses_data": [
            {"uuid": str(s.uuid), "name": s.name, "category": s.category}
            for s in statuses
        ],
        "labels_data": [
            {"uuid": str(label.uuid), "name": label.name, "color": label.color}
            for label in project.labels.all()
        ],
    }
    context["members_data"] = [
        {"id": str(m.user_id), "username": m.user.username} for m in context["members"]
    ]
    partial = request.GET.get("partial", "")
    if request.headers.get("X-Alpine-Request") and partial in ("board", "backlog"):
        return render(request, f"projects/ui/partials/{partial}.html", context)
    return render(request, "projects/ui/project.html", context)
