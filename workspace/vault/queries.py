from django.db.models import Q

from .models import Vault, VaultFolder, VaultKeyWrap, VaultRole, VaultTag


def user_vault_ids(user):
    """Return the UUIDs of the vaults *user* can open.

    A vault is reachable either by ownership or by holding a key wrap for
    it. Built as a UNION of two independently indexed queries for the same
    reason as ``calendar.queries.visible_calendar_ids``: an OR whose branch
    crosses a join defeats per-branch index use. The empty ``order_by()`` is
    required - ORDER BY is invalid inside a compound subquery - and the
    UNION dedups a vault the user both owns and holds a wrap for.
    """
    owned = Vault.objects.filter(owner=user).order_by().values_list("uuid", flat=True)
    wrapped = (
        VaultKeyWrap.objects.filter(recipient=user)
        .order_by()
        .values_list("vault_id", flat=True)
    )
    return list(owned.union(wrapped))


def get_vault_role(user, vault):
    """Return ``VaultRole.OWNER``, ``VaultRole.MEMBER`` or None.

    Ownership wins over a key wrap: an owner who also holds a wrap for
    their own vault is still an owner.
    """
    if vault.owner_id == user.pk:
        return VaultRole.OWNER
    if VaultKeyWrap.objects.filter(vault=vault, recipient=user).exists():
        return VaultRole.MEMBER
    return None


def accessible_entries_q(user):
    """Q filter over the entries of every vault *user* can open.

    Does NOT filter ``deleted_at``: the trash is a legitimate view, so the
    caller decides. Mirrors ``FileService.accessible_files_q``.
    """
    return Q(vault_id__in=user_vault_ids(user))


def visible_folders(user, vault):
    """Folders of *vault*, or an empty queryset if *user* cannot open it."""
    if get_vault_role(user, vault) is None:
        return VaultFolder.objects.none()
    return VaultFolder.objects.filter(vault=vault)


def visible_tags(user, vault):
    """Tags of *vault*, or an empty queryset if *user* cannot open it."""
    if get_vault_role(user, vault) is None:
        return VaultTag.objects.none()
    return VaultTag.objects.filter(vault=vault)
