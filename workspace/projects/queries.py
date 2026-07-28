from django.contrib.auth import get_user_model

from .models import Project, ProjectMember


def user_project_ids(user, *, role=None):
    """Return project UUIDs the user can access.

    ``role=None`` means any access: an active individual membership or
    membership of one of the project's attached auth.Groups.
    ``role='admin'`` narrows to projects where the user is an active admin
    member; group access never grants admin.

    Built as a UNION of two independently indexed queries for the same
    reason as ``calendar.queries.visible_calendar_ids``: an OR whose branch
    crosses a join defeats per-branch index use. The empty ``order_by()``
    is required, ORDER BY is invalid inside a compound subquery. UNION also
    dedups a project reachable through several of the user's groups.
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
    return list(member_ids.union(group_ids))


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
