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
    """A merge that would break a group invariant.

    Carries a stable `code` rather than prose: the wording belongs to the API
    layer, next to the other folder errors, and nothing derived from an
    exception should reach a response body.
    """

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _lock_folders(*folders):
    """Take row locks in primary-key order, then refresh the callers' copies.

    Ordering by primary key is what keeps two merges racing on an overlapping
    pair from deadlocking each other. Refreshing in place rather than handing
    back new rows keeps the instances the caller passed in usable afterwards.
    No-op on SQLite, which has no row locks.
    """
    pks = sorted({folder.pk for folder in folders})
    list(MailFolder.objects.select_for_update().filter(pk__in=pks).order_by("pk"))
    for folder in folders:
        folder.refresh_from_db()


def merge_folder(folder, into):
    """Make `folder` an alias of `into`. Returns the canonical folder."""
    if folder.pk == into.pk:
        raise MergeError("self")
    if folder.account_id != into.account_id:
        raise MergeError("cross_account")

    with transaction.atomic():
        # Validate under the lock, not before it. Two merges on an
        # overlapping pair would otherwise both read a clean state and both
        # commit, leaving a chain - and a folder two levels down is one no
        # read path resolves, so its mail shows up nowhere.
        _lock_folders(folder, into)
        if into.alias_of_id:
            raise MergeError("target_is_alias")
        if MailFolder.objects.filter(alias_of=folder).exists():
            raise MergeError("has_aliases")

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
        folder.is_hidden = into.is_hidden
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
    excluded = set(exclude_ids)
    with transaction.atomic():
        # The whole group is locked before an heir is picked: a merge landing
        # a new alias between the choice and the repoint would leave that
        # alias pointing at a canonical that is about to disappear.
        members = list(
            MailFolder.objects.select_for_update()
            .filter(alias_of=folder)
            .order_by("created_at")
        )
        heir = next((f for f in members if f.pk not in excluded), None)
        if heir is None:
            return None
        heir.alias_of = None
        heir.save(update_fields=["alias_of", "updated_at"])
        siblings = [f.pk for f in members if f.pk != heir.pk]
        if siblings:
            MailFolder.objects.filter(pk__in=siblings).update(alias_of=heir)
    logger.info(
        "Canonical folder %s is gone for %s; promoted %s",
        scrub(folder.name),
        scrub(folder.account.email),
        scrub(heir.name),
    )
    return heir
