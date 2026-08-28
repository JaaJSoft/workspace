from collections import defaultdict
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.charts import column_chart
from workspace.common.uuids import parse_uuid_or_none
from workspace.core.services.activity import annotate_time_ago
from workspace.projects.actions import ProjectActionRegistry
from workspace.projects.models import (
    Project,
    ProjectMember,
    ProjectNotificationLevel,
    Sprint,
    TaskAttachment,
    TaskStatus,
)
from workspace.projects.queries import (
    get_project_role,
    project_users,
    user_project_ids,
)
from workspace.projects.serializers import TaskAttachmentSerializer
from workspace.projects.services.analytics import (
    flow_summary,
    open_task_distribution,
    weekly_flow,
)
from workspace.projects.services.estimates import format_estimate
from workspace.projects.services.events import events_for_project, serialize_task_event
from workspace.projects.services.links import annotate_blocked, links_for_task
from workspace.projects.services.notification_levels import module_level
from workspace.projects.services.projects import get_or_create_personal_project
from workspace.projects.services.references import REFERENCE_RE
from workspace.projects.services.rendering import render_task_description
from workspace.projects.services.task_filters import (
    TaskFilterError,
    apply_task_filters,
    task_filters_active,
)
from workspace.projects.tasks import reminder_hour
from workspace.users.services.settings import get_setting, set_setting

# The board, backlog and all-tasks views deliberately render whole columns
# (drag-and-drop and bulk selection need every row of a column in the DOM)
# instead of paginating, so this explicit cap is the bound on how much one
# request renders. The templates show a notice when it truncates.
TASK_RENDER_LIMIT = 500

VIEW_OVERVIEW = "overview"
VIEW_BOARD = "board"
VIEW_BACKLOG = "backlog"
VIEW_TASKS = "tasks"
VIEW_ANALYTICS = "analytics"
VIEW_SETTINGS = "settings"

# The two board models a project can be converted between, in the order the
# settings picker shows them. Personal projects are absent on purpose: their
# type carries the one-per-user constraint, not a board layout.
BOARD_MODELS = [
    {
        "value": Project.Type.KANBAN,
        "label": "Kanban",
        "icon": "square-kanban",
        "description": "A continuous board where every column is always visible.",
    },
    {
        "value": Project.Type.SCRUM,
        "label": "Scrum",
        "icon": "timer",
        "description": "Timeboxed sprints; the board shows one sprint at a time.",
    },
]


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
    # created_by is rendered by the overview card; joining it here spares
    # every project view a lazy fetch.
    project = get_object_or_404(
        Project.objects.select_related("created_by"), uuid=project_uuid
    )
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


def _deep_link_panel(request, project, role, members):
    """Panel context for a valid ?task= deep link (UUID or WR-42), else empty."""
    raw = (request.GET.get("task") or "").strip()
    if not raw:
        return {}
    qs = project.tasks.select_related("status", "created_by", "epic").prefetch_related(
        "assignees", "labels"
    )
    task_uuid = parse_uuid_or_none(raw)
    if task_uuid is not None:
        task = qs.filter(uuid=task_uuid).first()
    else:
        task = _task_by_reference(qs, project, raw)
    if task is None:
        return {}
    return _task_panel_context(request.user, project, role, task, members=members)


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
    # Resolved once and handed down to the deep-link panel: project_users
    # costs two queries and the panel needs the same list.
    project_members = project_users(project)
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
        "epics_data": _epics_data(project),
        "members_data": [
            {
                "id": str(u.pk),
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
            }
            for u in project_members
        ],
        # In both render paths: the header bell re-renders on every
        # alpine-ajax view swap and must reflect the current state.
        "notification_level": {
            "override": ProjectNotificationLevel.objects.filter(
                project=project, user=request.user
            )
            .values_list("level", flat=True)
            .first()
            or "",
            "module_level": module_level(request.user),
        },
    }
    if not request.headers.get("X-Alpine-Request"):
        context["projects"] = _sidebar_projects(request.user)
        context["projects_prefs"] = {
            "reminder_hour": reminder_hour(request.user),
            "notify_level": module_level(request.user),
            "auto_watch": bool(
                get_setting(request.user, "projects", "auto_watch", default=True)
            ),
        }
        context["reminder_hours"] = [(h, f"{h:02d}:00") for h in range(24)]
        context.update(_deep_link_panel(request, project, role, project_members))
    return context


def _epics_data(project):
    # closed rides along so the pickers can offer open epics only while
    # closed ones still resolve to a name and color on cards and chips.
    return [
        {
            "uuid": str(epic.uuid),
            "name": epic.name,
            "color": epic.color,
            "closed": epic.is_closed,
        }
        for epic in project.epics.all()
    ]


def _filtered_tasks(request, qs):
    """Apply the shared task filters and the render cap to a task queryset.

    Returns (tasks, truncated); the one extra fetched row is what detects
    truncation without a COUNT query.
    """
    qs = apply_task_filters(qs, request.GET).distinct()
    tasks = list(qs[: TASK_RENDER_LIMIT + 1])
    return tasks[:TASK_RENDER_LIMIT], len(tasks) > TASK_RENDER_LIMIT


def _estimate_totals(request, project, qs):
    """Per-status estimate sums for the tasks of *qs* matching the filters.

    Aggregated over the full filtered queryset, not the rendered slice, so
    the totals stay true past the render cap. The uuid subquery flattens the
    M2M joins the filters may add - summing over duplicated rows would
    inflate the totals. A status whose tasks are all unestimated maps to
    None; callers fall back to 0.
    """
    if not project.estimate_unit:
        return {}
    filtered = apply_task_filters(qs, request.GET).values("uuid")
    return dict(
        project.tasks.filter(uuid__in=filtered)
        .values_list("status_id")
        .annotate(total=Sum("estimate"))
        .values_list("status_id", "total")
    )


def _record_visit(user, project_uuid):
    set_setting(user, "projects", "last_project", str(project_uuid))


def _render_project_view(request, context):
    if request.headers.get("X-Alpine-Request"):
        return render(request, "projects/ui/partials/_content.html", context)
    return render(request, "projects/ui/project.html", context)


def _task_panel_context(user, project, role, task, *, members=None):
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
    # Same send-time scope as the notification fan-out: watchers who lost
    # project or group access keep their row but are not shown.
    if members is None:
        members = project_users(project)
    allowed_ids = {u.pk for u in members if u.is_active}
    watch_rows = [
        w for w in task.watchers.select_related("user") if w.user_id in allowed_ids
    ]
    mine = next((w for w in watch_rows if w.user_id == user.pk), None)
    return {
        "panel_task": task,
        "panel_events": events,
        "panel_action_ids": action_ids,
        "panel_watch_state": (
            "" if mine is None else ("muted" if mine.muted else "watching")
        ),
        "panel_watchers": [
            {"id": str(w.user_id), "username": w.user.username}
            for w in watch_rows
            if not w.muted
        ],
        "panel_can_comment": "comment" in action_ids,
        "panel_comment_count": task.comments.count(),
        "panel_attachments": TaskAttachmentSerializer(
            task.attachments.select_related("task", "added_by"), many=True
        ).data,
        "panel_description_html": render_task_description(task.description),
        "panel_links": links_for_task(user, task),
        "panel_task_data": {
            "uuid": str(task.uuid),
            "project": str(project.uuid),
            "links_url": reverse(
                "project-task-links",
                kwargs={"project_uuid": project.uuid, "task_uuid": task.uuid},
            ),
            "link_search_url": reverse("project-tasks-search"),
            "title": task.title,
            "description": task.description,
            "status": str(task.status_id),
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else "",
            "estimate": format_estimate(task.estimate),
            "assignees": [str(u.pk) for u in task.assignees.all()],
            "assignee_users": [
                {"id": str(u.pk), "username": u.username} for u in task.assignees.all()
            ],
            "labels": [str(label.uuid) for label in task.labels.all()],
            "epic": str(task.epic_id) if task.epic_id else "",
            "subtasks": [
                {"uuid": str(s.uuid), "title": s.title, "done": s.done}
                for s in task.subtasks.all()
            ],
            "subtasks_url": reverse(
                "project-task-subtasks", args=[project.uuid, task.uuid]
            ),
        },
    }


@login_required
def task_panel(request, project_uuid, task_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    task = get_object_or_404(
        project.tasks.select_related("status", "created_by", "epic").prefetch_related(
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
        "epics_data": _epics_data(project),
    }
    context.update(_task_panel_context(request.user, project, role, task))
    return render(request, "projects/ui/partials/task_panel.html", context)


@login_required
def task_card(request, project_uuid, task_uuid):
    """Compact task card for the hover popover (calendar overlay, and any
    other surface that shows a task it cannot render in full)."""
    project, _role = _get_project_or_404(request.user, project_uuid)
    task = get_object_or_404(
        project.tasks.select_related("status", "epic").prefetch_related(
            "assignees", "labels"
        ),
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


def _sprint_context(request, project):
    """Sprint switcher context for the scrum board.

    The selection comes from ``?sprint=`` (active or closed sprints only -
    a planned sprint has no board yet), defaulting to the running sprint.
    No selectable sprint at all renders the empty state instead of the
    columns, and a closed selection renders read-only.
    """
    if project.type != Project.Type.SCRUM:
        return {}
    sprints = list(
        project.sprints.annotate(task_count=Count("tasks")).order_by("created_at")
    )
    active = [s for s in sprints if s.state == Sprint.State.ACTIVE]
    closed = [s for s in sprints if s.state == Sprint.State.CLOSED]
    # Active first, then history newest first: a retrospective opens the
    # last sprint, not sprint 1. Planned sprints have no board yet and are
    # started from the empty state, so they stay out of the switcher.
    switchable = active + closed[::-1]
    selected = None
    param = parse_uuid_or_none(request.GET.get("sprint") or "")
    if param is not None:
        selected = next((s for s in switchable if s.uuid == param), None)
    if selected is None:
        selected = next((s for s in sprints if s.state == Sprint.State.ACTIVE), None)
    context = {
        "sprints": switchable,
        "has_closed_sprints": bool(closed),
        "selected_sprint": selected,
        "sprint_read_only": selected is not None
        and selected.state == Sprint.State.CLOSED,
        "planned_sprints": [s for s in sprints if s.state == Sprint.State.PLANNED],
    }
    if selected is not None:
        # Read fresh by completeSprint() at dialog time: the island re-renders
        # with every board swap, unlike the page-load data islands.
        context["sprint_data"] = {
            "uuid": str(selected.uuid),
            "name": selected.name,
            "state": selected.state,
            "unfinished_count": selected.tasks.exclude(
                status__category=TaskStatus.Category.DONE
            ).count(),
            "planned": [
                {"uuid": str(s.uuid), "name": s.name}
                for s in context["planned_sprints"]
            ],
        }
    return context


@login_required
@ensure_csrf_cookie
def board(request, project_uuid):
    project, role = _get_project_or_404(request.user, project_uuid)
    _record_visit(request.user, project_uuid)
    context = _base_context(request, project, role, VIEW_BOARD)
    context["backlog_count"] = project.tasks.filter(
        status__category=TaskStatus.Category.BACKLOG
    ).count()
    context.update(_sprint_context(request, project))
    tasks_qs = annotate_blocked(
        project.tasks.exclude(status__category=TaskStatus.Category.BACKLOG)
        .select_related("status", "epic")
        .prefetch_related("assignees", "labels")
        # distinct=True: the task filters may join M2Ms, and duplicated rows
        # would otherwise inflate the checklist counters on the cards.
        .annotate(
            subtask_count=Count("subtasks", distinct=True),
            subtask_done_count=Count(
                "subtasks", filter=Q(subtasks__done=True), distinct=True
            ),
        )
        .order_by("position", "created_at")
    )
    if project.type == Project.Type.SCRUM:
        if context["selected_sprint"] is not None:
            tasks_qs = tasks_qs.filter(sprint=context["selected_sprint"])
        else:
            # No sprint to show: the template renders the empty state, and
            # an unfiltered queryset must not leak into the columns.
            tasks_qs = tasks_qs.none()
    hidden_counts = {}
    # A closed sprint is browsed as history: retention hiding would blank
    # out exactly the done tasks the reader came to see.
    if project.done_retention_days is not None and not context.get("sprint_read_only"):
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
    try:
        tasks, truncated = _filtered_tasks(request, tasks_qs)
        estimate_totals = _estimate_totals(request, project, tasks_qs)
    except TaskFilterError as exc:
        return HttpResponseBadRequest(f"Invalid {exc.field} parameter.")
    context["filters_active"] = task_filters_active(request.GET)
    context["tasks_truncated"] = truncated
    context["task_render_limit"] = TASK_RENDER_LIMIT
    tasks_by_status = defaultdict(list)
    for task in tasks:
        tasks_by_status[task.status_id].append(task)
    context["columns"] = [
        {
            "status": s,
            "tasks": tasks_by_status[s.pk],
            "hidden_count": hidden_counts.get(s.pk, 0),
            "estimate_total": estimate_totals.get(s.pk) or 0,
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
    if project.type == Project.Type.SCRUM:
        planning_sprints = list(
            project.sprints.exclude(state=Sprint.State.CLOSED).order_by("created_at")
        )
        context["planning_sprints"] = planning_sprints
        has_active_sprint = any(
            s.state == Sprint.State.ACTIVE for s in planning_sprints
        )
        # Gates the per-row board shortcut (the bulk bar is sprint-only on
        # scrum): without a running sprint the board shows nothing, so
        # sending a task there would make it invisible.
        context["hide_send_to_board"] = not has_active_sprint
        # ?sprint= scopes the list like the board switcher: an open sprint's
        # UUID, or the literal "none" for the unplanned pool. Anything else
        # (closed, foreign, malformed) falls back to the whole backlog.
        scope_param = request.GET.get("sprint") or ""
        if scope_param == "none":
            context["backlog_scope"] = "none"
        else:
            parsed = parse_uuid_or_none(scope_param)
            context["backlog_scope"] = next(
                (s for s in planning_sprints if s.uuid == parsed), None
            )
    backlog_qs = (
        project.tasks.filter(status__category=TaskStatus.Category.BACKLOG)
        .select_related("status", "epic", "sprint")
        .prefetch_related("assignees", "labels")
        .order_by("position", "created_at")
    )
    if context.get("backlog_scope") == "none":
        backlog_qs = backlog_qs.filter(sprint__isnull=True)
    elif context.get("backlog_scope"):
        backlog_qs = backlog_qs.filter(sprint=context["backlog_scope"])
    try:
        backlog_tasks, truncated = _filtered_tasks(request, backlog_qs)
        estimate_totals = _estimate_totals(request, project, backlog_qs)
    except TaskFilterError as exc:
        return HttpResponseBadRequest(f"Invalid {exc.field} parameter.")
    context["backlog_tasks"] = backlog_tasks
    context["estimate_total"] = sum(
        total for total in estimate_totals.values() if total
    )
    context["filters_active"] = task_filters_active(request.GET)
    context["tasks_truncated"] = truncated
    context["task_render_limit"] = TASK_RENDER_LIMIT
    # The sidebar badge counts the whole backlog, not the filtered slice.
    context["backlog_count"] = project.tasks.filter(
        status__category=TaskStatus.Category.BACKLOG
    ).count()
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
    try:
        all_tasks_list, truncated = _filtered_tasks(
            request,
            project.tasks.select_related("status", "epic", "sprint")
            .prefetch_related("assignees", "labels")
            .order_by(
                "status__position", "status__created_at", "position", "created_at"
            ),
        )
    except TaskFilterError as exc:
        return HttpResponseBadRequest(f"Invalid {exc.field} parameter.")
    context["all_tasks"] = all_tasks_list
    context["filters_active"] = task_filters_active(request.GET)
    context["tasks_truncated"] = truncated
    context["task_render_limit"] = TASK_RENDER_LIMIT
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
                        "css_class": "fill-info",
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
        "estimate_unit": project.estimate_unit,
        "groups": [
            {"id": group.pk, "name": group.name}
            for group in project.groups.order_by("name")
        ],
        "type": project.type,
        "archived": project.is_archived,
    }
    context["board_models"] = BOARD_MODELS
    return _render_project_view(request, context)


@login_required
def view_attachment(request, attachment_uuid):
    """Render viewer HTML for a task attachment (read-only)."""
    attachment = (
        TaskAttachment.objects.select_related("task")
        .filter(uuid=attachment_uuid)
        .first()
    )
    if attachment is None or attachment.task.project_id not in user_project_ids(
        request.user
    ):
        raise Http404

    from django.http import HttpResponse

    from workspace.files.services.filetype import get_viewer_by_slug
    from workspace.files.ui.viewers import ViewerRegistry, render_viewer_panel

    # A pinned viewer wins; an unknown pin degrades to content-based
    # resolution rather than breaking the modal.
    ViewerClass = get_viewer_by_slug(attachment.viewer) or ViewerRegistry.get_viewer(
        attachment.type, attachment.original_name
    )
    if not ViewerClass:
        return HttpResponse(
            render_viewer_panel(
                '<div class="p-8 text-center text-error">'
                f"No viewer available for {attachment.type}</div>"
            ),
            status=400,
        )

    class AttachmentAdapter:
        def __init__(self, att):
            self.uuid = att.uuid
            self.name = att.original_name
            self.mime_type = att.mime_type
            self.type = att.type
            self.category = att.category
            self.content = att.file

        def is_viewable(self):
            return True

    viewer = ViewerClass(AttachmentAdapter(attachment))
    viewer._user_can_edit = False
    viewer._content_url = reverse(
        "project-task-attachment-download",
        kwargs={
            "project_uuid": attachment.task.project_id,
            "task_uuid": attachment.task_id,
            "uuid": attachment.uuid,
        },
    )
    return HttpResponse(render_viewer_panel(viewer.render(request)))
