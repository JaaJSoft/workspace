from django.contrib.auth import get_user_model
from django.db.models import Case, F, IntegerField, Value, When
from django.utils import timezone

from .models import Project, ProjectMember, Task, TaskStatus

# CharField priorities don't sort by urgency ('high' < 'urgent' alphabetically),
# so ordering needs an explicit rank. Lower rank = more urgent.
_PRIORITY_RANK = Case(
    When(priority=Task.Priority.URGENT, then=Value(0)),
    When(priority=Task.Priority.HIGH, then=Value(1)),
    When(priority=Task.Priority.MEDIUM, then=Value(2)),
    default=Value(3),
    output_field=IntegerField(),
)


def user_project_ids(user, *, role=None):
    """Return project UUIDs the user can access.

    ``role=None`` means any access: an active individual membership or
    membership of one of the project's attached auth.Groups.
    ``role='admin'`` narrows to projects where the user is an active admin
    member; group access never grants admin.

    Built as a UNION of two independently indexed queries for the same
    reason as ``calendar.queries.visible_calendar_ids``: an OR whose branch
    crosses a join defeats per-branch index use. The empty ``order_by()``
    is required on each branch (ORDER BY is invalid inside a compound
    subquery) and on the combined queryset (a combined queryset falls back
    to the first arm's ``Meta.ordering``; ``ProjectMember`` has none today,
    but the fallback would break the single-column result set the moment it
    grows one - see ``visible_calendar_ids``). UNION also dedups a project
    reachable through several of the user's groups.
    """
    memberships = ProjectMember.objects.filter(user=user, left_at__isnull=True)
    if role is not None:
        memberships = memberships.filter(role=role)
        return memberships.values_list("project_id", flat=True)
    member_ids = memberships.order_by().values_list("project_id", flat=True)
    group_ids = (
        Project.objects.filter(groups__in=user.groups.all())
        .order_by()
        .values_list("uuid", flat=True)
    )
    return list(member_ids.union(group_ids).order_by())


def due_open_tasks():
    """Open tasks across all projects that are overdue or due today.

    Feeds the due-task notification cron: archived projects are excluded
    because nobody can act on their tasks anymore. Access is not filtered
    here - the cron resolves each task's audience per project.
    """
    return (
        Task.objects.filter(
            project__archived_at__isnull=True,
            due_date__lte=timezone.localdate(),
        )
        .exclude(status__category=TaskStatus.Category.DONE)
        .select_related("project")
    )


def tasks_due_between(user, start, end):
    """Open tasks due in ``[start, end)`` across every project *user* can see.

    Feeds the calendar module's task overlay, which queries this at render
    time instead of mirroring due dates into ``Event`` rows - a due date
    moved on the board shows up on the next fetch with nothing to sync and
    nothing to drift. ``end`` is exclusive to match FullCalendar's range.

    Accessible, non-archived projects only, with no assignee filter:
    the calendar shows the whole team's deadlines, not just the viewer's.
    """
    return (
        Task.objects.filter(
            project_id__in=user_project_ids(user),
            project__archived_at__isnull=True,
            due_date__gte=start,
            due_date__lt=end,
        )
        .exclude(status__category=TaskStatus.Category.DONE)
        .select_related("project")
        .order_by("due_date", "position")
    )


def assigned_open_tasks(user):
    """Open tasks assigned to *user*, most urgent first.

    Accessible, non-archived projects only, without a due-date cutoff:
    this feeds the dashboard task list, which also shows upcoming and
    undated work. Ordered by due
    date (overdue first, undated last), then priority, then age. Project
    and status are joined for ``task.reference`` and status display.
    """
    return (
        Task.objects.filter(
            assignees=user,
            project_id__in=user_project_ids(user),
            project__archived_at__isnull=True,
        )
        .exclude(status__category=TaskStatus.Category.DONE)
        .select_related("project", "status")
        .annotate(priority_rank=_PRIORITY_RANK)
        .order_by(F("due_date").asc(nulls_last=True), "priority_rank", "created_at")
    )


def project_users(project):
    """Users who can access *project*: active individual members plus
    members of the attached auth.Groups, deduplicated, sorted by username.

    The reverse direction of ``user_project_ids``; keep the two in sync.
    """
    memberships = ProjectMember.objects.filter(
        project=project, left_at__isnull=True
    ).select_related("user")
    users = {m.user_id: m.user for m in memberships}
    group_users = (
        get_user_model()
        .objects.filter(groups__in=project.groups.all())
        .exclude(pk__in=users.keys())
        .distinct()
    )
    for user in group_users:
        users[user.pk] = user
    return sorted(users.values(), key=lambda u: u.username.lower())


def get_project_role(user, project):
    """Return the user's role on *project*: 'admin', 'member', or None.

    An active membership row wins over group access; group access grants
    'member' only.
    """
    membership = (
        ProjectMember.objects.filter(project=project, user=user, left_at__isnull=True)
        .only("role")
        .first()
    )
    if membership is not None:
        return membership.role
    if user.groups.filter(projects=project).exists():
        return ProjectMember.Role.MEMBER
    return None
