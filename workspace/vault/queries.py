from django.db.models import Q

from .models import (
    AccountIdentity,
    Vault,
    VaultFolder,
    VaultKeyWrap,
    VaultRole,
    VaultTag,
)


def user_vault_ids(user):
    """Return the UUIDs of the vaults *user* can open.

    A vault is reachable either by ownership or by holding a key wrap for
    it. Built as a UNION of two independently indexed queries for the same
    reason as ``calendar.queries.visible_calendar_ids``: an OR whose branch
    crosses a join defeats per-branch index use, and the UNION dedups a
    vault the user both owns and holds a wrap for.

    All three empty ``order_by()`` calls are load-bearing: the two inner
    ones because ORDER BY is invalid inside a compound subquery, the outer
    one because ``union()`` hands the compound query back the model's
    ``Meta.ordering`` - a sort on ``created_at``, which the compound query
    does not select, and which the database therefore rejects.
    """
    owned = Vault.objects.filter(owner=user).order_by().values_list("uuid", flat=True)
    wrapped = (
        VaultKeyWrap.objects.filter(recipient=user)
        .order_by()
        .values_list("vault_id", flat=True)
    )
    return list(owned.union(wrapped).order_by())


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


def reachable_vault(user, vault_uuid):
    """The vault *user* can open under that UUID, or None.

    One helper rather than a role check written out in every view: a caller
    that gets None must answer 404 without saying which of "no such vault" and
    "not yours" applied, and centralising it is what keeps the two answers
    indistinguishable.
    """
    vault = Vault.objects.filter(uuid=vault_uuid).first()
    if vault is None or get_vault_role(user, vault) is None:
        return None
    return vault


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


def active_identity(user):
    """The user's finished cryptographic identity, or None.

    A pending row does not count: ``init`` created it and the browser never
    came back with the sealed private keys, so the account can seal nothing
    and open nothing.
    """
    return AccountIdentity.objects.filter(
        user=user, state=AccountIdentity.State.ACTIVE
    ).first()
