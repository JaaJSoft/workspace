from .models import MailAccount, MailFolder


def user_account_ids(user):
    """Return mail account UUIDs owned by the user."""
    return MailAccount.objects.filter(owner=user).values_list("uuid", flat=True)


def canonical_folder(folder):
    """The folder a merge group is keyed on.

    Identity for a folder that is not an alias, so callers never branch.
    """
    return folder.alias_of or folder


def canonical_folder_id(folder):
    """The group's key without fetching the canonical row.

    For hot loops (batch moves) where `canonical_folder` would cost one
    query per message.
    """
    return folder.alias_of_id or folder.pk


def folder_group_ids(folder):
    """UUIDs of `folder`'s canonical folder and every folder merged into it.

    Resolves an alias argument to its canonical first: URLs, saved rules and
    AI tool calls carry folder UUIDs captured before a merge, and they have
    to keep addressing the whole group rather than half of it.
    """
    root = canonical_folder(folder)
    return [
        root.pk,
        *MailFolder.objects.filter(alias_of=root).values_list("uuid", flat=True),
    ]


def special_folder(account, folder_type):
    """The account's canonical folder of that type, or None.

    Restricted to canonicals: with a duplicate pair sharing a folder_type,
    an unrestricted `.first()` resolves through Meta.ordering and files mail
    into whichever name sorts first alphabetically.
    """
    return MailFolder.objects.filter(
        account=account, folder_type=folder_type, alias_of__isnull=True
    ).first()
