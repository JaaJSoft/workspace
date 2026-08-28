"""Merging duplicate folders of one account under a canonical one.

Purely local: nothing here talks to IMAP. An alias keeps its own mailbox, its
own UIDs and its own sync state - only the way the app reads and addresses it
changes. Every write to `alias_of` goes through this module, because the
invariants it holds cannot be expressed as database constraints: they need a
second row's columns.
"""

import logging

from django.db import transaction

from workspace.common.logging import scrub

from ..models import MailFolder
from .imap_mailbox import _detect_folder_type

logger = logging.getLogger(__name__)


class MergeError(ValueError):
    """A merge that would break a group invariant."""


def merge_folder(folder, into):
    """Make `folder` an alias of `into`. Returns the canonical folder."""
    if folder.pk == into.pk:
        raise MergeError("A folder cannot be merged into itself.")
    if folder.account_id != into.account_id:
        raise MergeError("Only folders of the same account can be merged.")
    if into.alias_of_id:
        raise MergeError("The target folder is already merged into another one.")
    if MailFolder.objects.filter(alias_of=folder).exists():
        raise MergeError("Unmerge this folder's own aliases before merging it.")

    with transaction.atomic():
        # A canonical typed `other` takes the alias's special type: merging
        # `Sent` into a user-created `Envoyes` has to leave the account with a
        # sent folder, or nothing can file the sent copy any more.
        if (
            into.folder_type == MailFolder.FolderType.OTHER
            and folder.folder_type != MailFolder.FolderType.OTHER
        ):
            into.folder_type = folder.folder_type
            into.save(update_fields=["folder_type", "updated_at"])

        folder.alias_of = into
        folder.folder_type = into.folder_type
        # The group's visibility is the canonical's. A hidden alias would be
        # excluded from search while the folder it belongs to stays in it.
        folder.is_hidden = False
        folder.save(
            update_fields=["alias_of", "folder_type", "is_hidden", "updated_at"]
        )
    return into


def unmerge_folder(folder):
    """Detach `folder` from its group. Returns it, standalone."""
    if not folder.alias_of_id:
        return folder
    # Group visibility governs while grouped; the last group visibility
    # sticks on detach. The user hid that mail, so detaching a member must
    # not resurface it.
    folder.is_hidden = folder.alias_of.is_hidden
    folder.alias_of = None
    # Back to what its own name says. Keeping the inherited type would put a
    # second trash-typed folder next to the real Trash.
    folder.folder_type = _detect_folder_type(folder.name, "")
    folder.save(update_fields=["alias_of", "folder_type", "is_hidden", "updated_at"])
    return folder


def set_group_hidden(folder, is_hidden):
    """Apply the canonical's visibility to its whole group.

    Search and the notification filter both key on the message's physical
    folder, so an alias left visible under a hidden canonical keeps
    notifying and keeps surfacing in search.
    """
    with transaction.atomic():
        folder.is_hidden = is_hidden
        folder.save(update_fields=["is_hidden", "updated_at"])
        if folder.alias_of_id is None:
            MailFolder.objects.filter(alias_of=folder).update(is_hidden=is_hidden)


def promote_alias(folder, exclude_ids=()):
    """Hand `folder`'s group to its oldest alias, before `folder` goes away.

    Returns the promoted folder, or None when the group is empty. Called
    wherever a canonical row is about to disappear - discovery no longer
    listing the mailbox, or an explicit delete - so mail the user could see
    yesterday stays where they left it instead of the sidebar silently
    regrowing the duplicates they merged away.

    `exclude_ids` keeps discovery from crowning an heir that is disappearing
    in the same pass.
    """
    heir = (
        MailFolder.objects.filter(alias_of=folder)
        .exclude(pk__in=exclude_ids)
        .order_by("created_at")
        .first()
    )
    if heir is None:
        return None
    with transaction.atomic():
        heir.alias_of = None
        heir.save(update_fields=["alias_of", "updated_at"])
        MailFolder.objects.filter(alias_of=folder).exclude(pk=heir.pk).update(
            alias_of=heir
        )
    logger.info(
        "Canonical folder %s is gone for %s; promoted %s",
        scrub(folder.name),
        scrub(folder.account.email),
        scrub(heir.name),
    )
    return heir
