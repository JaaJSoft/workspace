from django.utils import timezone

from ..models import Project, ProjectMember


class ProjectRuleError(Exception):
    """Violation of a project business rule; the API maps it to HTTP 400."""


class LastAdminError(ProjectRuleError):
    """A project must always keep at least one active admin."""


def _other_active_admins(member):
    return ProjectMember.objects.filter(
        project_id=member.project_id,
        role=ProjectMember.Role.ADMIN,
        left_at__isnull=True,
    ).exclude(pk=member.pk)


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
    if (
        member.role == ProjectMember.Role.ADMIN
        and new_role != ProjectMember.Role.ADMIN
        and not _other_active_admins(member).exists()
    ):
        raise LastAdminError("Cannot demote the last admin of a project.")
    member.role = new_role
    member.save(update_fields=["role"])
    return member


def remove_member(member):
    """Deactivate a membership (sets left_at); also used for self-leave."""
    if (
        member.role == ProjectMember.Role.ADMIN
        and member.left_at is None
        and not _other_active_admins(member).exists()
    ):
        raise LastAdminError(
            "Cannot remove the last admin of a project. "
            "Promote another admin first or delete the project."
        )
    member.left_at = timezone.now()
    member.save(update_fields=["left_at"])
    return member
