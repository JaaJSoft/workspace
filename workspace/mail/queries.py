from .models import MailAccount, MailAttachment, MailFolder


def user_account_ids(user):
    """Return mail account UUIDs owned by the user."""
    return MailAccount.objects.filter(owner=user).values_list("uuid", flat=True)


def resolve_reachable_attachments(user, attachment_uuids):
    """Resolve *attachment_uuids* to the user's own MailAttachment rows, or None.

    Deduplicates the input and keeps its order. Returns None as soon as one
    uuid is unknown or belongs to a message of someone else's account:
    callers attach all-or-nothing and must not tell the two apart.
    """
    attachment_uuids = list(dict.fromkeys(attachment_uuids))
    if not attachment_uuids:
        return []
    by_uuid = {
        attachment.uuid: attachment
        for attachment in MailAttachment.objects.filter(
            uuid__in=attachment_uuids,
            message__account_id__in=user_account_ids(user),
        )
    }
    if len(by_uuid) != len(attachment_uuids):
        return None
    return [by_uuid[uuid] for uuid in attachment_uuids]


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
