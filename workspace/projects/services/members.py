from django.db import transaction
from django.utils import timezone

from ..models import Project, ProjectMember


class ProjectRuleError(Exception):
    """Violation of a project business rule; the API maps it to HTTP 400.

    ``detail`` is a curated, user-facing message built from constant strings
    (never from user input or tracebacks), safe to return in API responses.
    """

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class LastAdminError(ProjectRuleError):
    """A project must always keep at least one active admin."""


def _other_active_admins_locked(member):
    """Lock the project's active-admin rows, then report whether another
    active admin exists.

    Locking the full set (member's own row included) is what serializes two
    concurrent demotions/removals targeting each other: both transactions
    contend on the same rows, so the loser re-reads the winner's result
    instead of acting on a stale snapshot. Requires an open transaction.
    """
    admin_pks = list(
        ProjectMember.objects.select_for_update()
        .filter(
            project_id=member.project_id,
            role=ProjectMember.Role.ADMIN,
            left_at__isnull=True,
        )
        .values_list("pk", flat=True)
    )
    return any(pk != member.pk for pk in admin_pks)


def add_member(project, user, *, role=ProjectMember.Role.MEMBER):
    """Add *user* to *project*.

    If the user is new to the project, creates a membership at the given role.
    If the user previously left, reactivates their membership at the given role.
    If the user is already an active member, delegates to change_member_role to
    apply the role change with guards (e.g., cannot demote the last active admin).
    """
    if project.type == Project.Type.PERSONAL:
        raise ProjectRuleError("Personal projects cannot have members.")
    member, created = ProjectMember.objects.get_or_create(
        project=project,
        user=user,
        defaults={"role": role},
    )
    if not created:
        if member.left_at is not None:
            # Reactivate a departed membership row.
            member.role = role
            member.left_at = None
            member.save(update_fields=["role", "left_at"])
        else:
            # Member is active; route through change_member_role for guard checks.
            return change_member_role(member, role)
    return member


def change_member_role(member, new_role):
    with transaction.atomic():
        if (
            member.role == ProjectMember.Role.ADMIN
            and new_role != ProjectMember.Role.ADMIN
            and not _other_active_admins_locked(member)
        ):
            raise LastAdminError("Cannot demote the last admin of a project.")
        member.role = new_role
        member.save(update_fields=["role"])
    return member


def remove_member(member):
    """Deactivate a membership (sets left_at); also used for self-leave."""
    with transaction.atomic():
        if (
            member.role == ProjectMember.Role.ADMIN
            and member.left_at is None
            and not _other_active_admins_locked(member)
        ):
            raise LastAdminError(
                "Cannot remove the last admin of a project. "
                "Promote another admin first or delete the project."
            )
        member.left_at = timezone.now()
        member.save(update_fields=["left_at"])
    return member
