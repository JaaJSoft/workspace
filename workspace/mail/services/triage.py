"""Act on one message: flag it, file it, label it.

Each of these pairs a local write with its remote counterpart, and the
pairing is the whole point - the two halves fail differently, and the rule
for what to do about it is per-action, not per-caller. The mail UI, the REST
endpoints and the assistant's triage tools all go through here so a message
cannot end up flagged in one place and not the other depending on which
button moved it.

Bulk paths (`MailBatchActionView`) deliberately keep their own loop: they
collapse N refreshes into one aggregate, which a per-message helper cannot
do. They still take their IMAP move from `move_to_folder` - the invariant
below is not something to write twice.
"""

import logging
from collections import defaultdict

from django.db import IntegrityError, transaction
from django.utils import timezone

from workspace.common.logging import scrub

from ..models import MailFolder
from .counts import (
    refresh_folder_counts,
    refresh_folders_counts_bulk,
    refresh_label_counts,
    refresh_labels_for_messages,
)

logger = logging.getLogger(__name__)

# Folders a message has nowhere to be archived or reported out of. The mail
# page reads them too, to offer Archive and Report spam where these do.
ARCHIVE_REFUSED_FROM = (
    MailFolder.FolderType.ARCHIVE,
    MailFolder.FolderType.TRASH,
    MailFolder.FolderType.SPAM,
    MailFolder.FolderType.DRAFTS,
)
SPAM_REFUSED_FROM = (
    MailFolder.FolderType.SPAM,
    MailFolder.FolderType.TRASH,
    MailFolder.FolderType.DRAFTS,
    MailFolder.FolderType.SENT,
)


def flag_operations():
    """``{name: (imap_call, imap_flag, field, value)}`` for each message flag.

    `value` is also whether the IMAP flag is added or removed. Exposed so the
    bulk path can drive its own loop off the same table instead of
    restating which IMAP flag goes with which column.
    """
    from .imap_messages import (
        ANSWERED,
        FLAGGED,
        SEEN,
        mark_answered,
        mark_read,
        mark_unread,
        star_message,
        unstar_message,
    )

    return {
        "read": (mark_read, SEEN, "is_read", True),
        "unread": (mark_unread, SEEN, "is_read", False),
        "starred": (star_message, FLAGGED, "is_starred", True),
        "unstarred": (unstar_message, FLAGGED, "is_starred", False),
        "answered": (mark_answered, ANSWERED, "is_answered", True),
    }


def set_flag(message, flag):
    """Apply one flag to `message` locally and mirror it to IMAP.

    Returns True when the server took it, False when only the local row
    moved. The local write happens either way, on purpose: the mail UI is
    optimistic here and the next sync reconciles. A caller that wants to
    tell the user about the drift reads the return value.
    """
    from .imap_sync import forget_remote_state

    remote, _imap_flag, field, value = flag_operations()[flag]
    synced = True
    setattr(message, field, value)
    try:
        remote(message.account, message)
    except Exception:
        synced = False

    with transaction.atomic():
        message.save(update_fields=[field, "updated_at"])
        if not synced:
            forget_remote_state([message.folder_id])
        refresh_folder_counts(message.folder)
        if field == "is_read":
            refresh_labels_for_messages([message.pk])
    return synced


def mark_folder_read(folder):
    """Mark every unread message of `folder`'s merge group read, here and on IMAP.

    Returns ``(updated, synced)``: how many rows turned read, and whether
    every mailbox of the group took the flags. Same contract as `set_flag`:
    the server is asked first, the local write happens either way, and a
    mailbox that refused the flags flips its messages back to unread on the
    next sync.
    """
    from ..models import MailMessage
    from ..queries import folder_group_ids
    from .imap_connection import shared_connections
    from .imap_messages import mark_uids_read
    from .imap_sync import forget_remote_state

    group_ids = folder_group_ids(folder)
    unread = MailMessage.objects.filter(
        folder_id__in=group_ids,
        is_read=False,
        deleted_at__isnull=True,
    )
    uids_by_folder = defaultdict(list)
    for folder_id, uid in unread.values_list("folder_id", "imap_uid"):
        uids_by_folder[folder_id].append(uid)

    refused = []
    with shared_connections():
        for mailbox in MailFolder.objects.filter(uuid__in=uids_by_folder):
            try:
                mark_uids_read(folder.account, mailbox, uids_by_folder[mailbox.pk])
            except Exception as e:
                refused.append(mailbox.pk)
                logger.warning(
                    "IMAP mark-all-read failed for folder %s: %s",
                    scrub(mailbox.name),
                    scrub(e),
                )

    # Only the rows the STORE flagged: matched by the folder and UID it was
    # sent, not by primary key. A message the sync imported during the IMAP
    # calls, or one moved out of the folder (and maybe back, under a new UID)
    # meanwhile, never got its \Seen, and the next sync would flip it back
    # to unread. A UID is unique in its folder and never reused, so the pair
    # names exactly the message the server flagged.
    updated = 0
    now = timezone.now()
    with transaction.atomic():
        for folder_id, uids in uids_by_folder.items():
            flagged = MailMessage.objects.filter(
                folder_id=folder_id,
                imap_uid__in=uids,
                deleted_at__isnull=True,
                is_read=False,
            )
            pks = list(flagged.values_list("pk", flat=True))
            updated += MailMessage.objects.filter(pk__in=pks).update(
                is_read=True, updated_at=now
            )
            refresh_labels_for_messages(pks)
        forget_remote_state(refused)
        refresh_folders_counts_bulk(group_ids)
    return updated, not refused


def move_to_folder(message, target, refresh=True):
    """Move `message` into `target` on the server, then re-point the row.

    Raises whatever the IMAP layer raises, having written nothing locally.
    That order is the invariant: re-pointing the row while the server still
    holds the message in its old folder gives the next sync a message it
    cannot find where we claim it is, and it soft-deletes it.

    The row takes the UID the server gave the copy, so the target's sync
    recognises it rather than soft-deleting it and importing it again as a
    stranger - without labels, summary or extractions, and with rules and
    notifications firing a second time. When that UID is unknown the row
    is soft-deleted in place, as the source's reconcile would have done:
    keeping the old UID in the target would point it at whichever message
    holds that number there, and a later flag or delete with it.

    Returns the folder the message came from. `refresh=False` leaves the
    folder counters to a caller batching several moves into one aggregate;
    the label counters of a retired row are refreshed here either way, since
    only this function knows the row was retired.
    """
    from ..models import MailMessage
    from .imap_messages import move_message
    from .notifications import settle_message_notifications

    copied_uid = move_message(message.account, message, target)

    source_id = message.folder_id
    # A concurrent move of the same message may have re-pointed the row while
    # this one talked to the server: its COPY of the UID the other expunged
    # answers OK with nothing copied. Every write below is conditional on the
    # row still being where this request found it, so the loser touches
    # nothing.
    still_in_source = MailMessage.objects.filter(
        pk=message.pk,
        folder_id=source_id,
        imap_uid=message.imap_uid,
        deleted_at__isnull=True,
    )
    now = timezone.now()
    with transaction.atomic():
        repointed = 0
        # A sync of the target that ran between the COPY and here has already
        # imported the copy under that UID - before the check, or after it,
        # which only the unique constraint catches.
        if (
            copied_uid is not None
            and not MailMessage.objects.filter(
                folder=target, imap_uid=copied_uid
            ).exists()
        ):
            try:
                with transaction.atomic():
                    repointed = still_in_source.update(
                        folder=target, imap_uid=copied_uid, updated_at=now
                    )
            except IntegrityError:
                repointed = 0
            if repointed:
                message.folder = target
                message.imap_uid = copied_uid
                message.updated_at = now
        if not repointed and still_in_source.update(deleted_at=now, updated_at=now):
            message.deleted_at = now
            message.updated_at = now
            settle_message_notifications(message.account.owner, [message.pk])
            refresh_labels_for_messages([message.pk])
        if refresh:
            refresh_folders_counts_bulk({source_id, target.pk})
    return source_id


def archive_message(message, refresh=True):
    """File `message` into the account's Archive. True when it went.

    An account without an Archive folder gets one on the server, except
    where the server keeps every message in an all-mail mailbox (Gmail's
    All Mail, which the sync skips): there archiving is taking the message
    out of the Inbox, and a message in any other folder has nothing to be
    archived out of. False, with nothing touched, for a message that has
    nowhere to go - already archived, in Trash, Spam or Drafts.

    Raises whatever the IMAP layer raises, having written nothing locally
    (the `move_to_folder` invariant).
    """
    from ..queries import canonical_folder_id, special_folder
    from .imap_folders import create_archive_folder

    types = MailFolder.FolderType
    source_type = message.folder.folder_type
    if source_type in ARCHIVE_REFUSED_FROM:
        return False
    account = message.account
    archive = special_folder(account, types.ARCHIVE)
    if archive is None and account.archives_in_all_mail:
        if source_type != types.INBOX:
            return False
        _take_out_of_folder(message, refresh)
        return True
    if archive is None:
        archive = create_archive_folder(account)
    if canonical_folder_id(message.folder) == archive.pk:
        return False
    move_to_folder(message, archive, refresh=refresh)
    return True


def report_spam(message, refresh=True):
    """Move `message` into the account's Spam folder. True when it went.

    False, with nothing touched, on an account without one and for a
    message that is spam already, trashed, a draft or sent by the user.
    """

    if message.folder.folder_type in SPAM_REFUSED_FROM:
        return False
    return _move_to_special_folder(message, MailFolder.FolderType.SPAM, refresh)


def mark_not_spam(message, refresh=True):
    """Move `message` out of Spam, back into the Inbox. True when it went."""

    types = MailFolder.FolderType
    if message.folder.folder_type != types.SPAM:
        return False
    return _move_to_special_folder(message, types.INBOX, refresh)


def _move_to_special_folder(message, folder_type, refresh):
    from ..queries import canonical_folder_id, special_folder

    target = special_folder(message.account, folder_type)
    if target is None or canonical_folder_id(message.folder) == target.pk:
        return False
    move_to_folder(message, target, refresh=refresh)
    return True


def _take_out_of_folder(message, refresh):
    """Expunge `message` from its mailbox, then retire its row.

    Server first, as in `move_to_folder`: a refused expunge raises with the
    row untouched. Only for a server that keeps the message elsewhere.
    """
    from ..models import MailMessage
    from .imap_messages import delete_message
    from .notifications import settle_message_notifications

    delete_message(message.account, message)

    now = timezone.now()
    with transaction.atomic():
        retired = MailMessage.objects.filter(
            pk=message.pk,
            folder_id=message.folder_id,
            imap_uid=message.imap_uid,
            deleted_at__isnull=True,
        ).update(deleted_at=now, updated_at=now)
        if retired:
            message.deleted_at = now
            message.updated_at = now
            settle_message_notifications(message.account.owner, [message.pk])
            refresh_labels_for_messages([message.pk])
        if refresh:
            refresh_folder_counts(message.folder)


TRASHED = "trashed"
EXPUNGED = "expunged"


def trash_message(message, refresh=True, expunge=True):
    """Delete `message` the way a mail client does: into the account's Trash.

    Outside Trash the message is moved there with `move_to_folder`, so it
    stays recoverable, on the server and from every other client. Only a
    message already in Trash, or on an account that has none, is deleted for
    good - and only when `expunge` allows it: an automated caller passes
    False and gets None back, with nothing touched.

    Returns TRASHED, EXPUNGED or None. A move to Trash raises whatever the
    IMAP layer raises, having written nothing locally (the `move_to_folder`
    invariant). An expunge retires the row first and asks the server after:
    a server that refuses leaves the message where the next sync finds it
    under a row already soft-deleted, so the failure is only logged.

    The message's notification is settled either way: the user is done with
    it, and a trashed message no longer shows up on the page that would
    mark it read. `refresh=False` leaves the folder and label counters to a
    caller batching several deletes.
    """
    from ..queries import canonical_folder_id, special_folder
    from .imap_messages import delete_message
    from .notifications import settle_message_notifications

    owner = message.account.owner
    trash = special_folder(message.account, MailFolder.FolderType.TRASH)
    if trash is not None and canonical_folder_id(message.folder) != trash.pk:
        move_to_folder(message, trash, refresh=refresh)
        settle_message_notifications(owner, [message.pk])
        return TRASHED
    if not expunge:
        return None

    with transaction.atomic():
        message.deleted_at = timezone.now()
        message.save(update_fields=["deleted_at", "updated_at"])
        if refresh:
            refresh_folder_counts(message.folder)
            refresh_labels_for_messages([message.pk])
    settle_message_notifications(owner, [message.pk])
    try:
        delete_message(message.account, message)
    except Exception as e:
        logger.warning("IMAP delete failed for message %s: %s", message.uuid, scrub(e))
    return EXPUNGED


def set_label(message, label, attached):
    """Attach `label` to `message` or take it off. True when it changed.

    Labels are ours, not the server's, so there is no remote half here -
    only the counter that has to follow.
    """
    from ..models import MailMessageLabel

    with transaction.atomic():
        if attached:
            _, changed = MailMessageLabel.objects.get_or_create(
                message=message, label=label
            )
        else:
            removed, _ = MailMessageLabel.objects.filter(
                message=message, label=label
            ).delete()
            changed = bool(removed)
        refresh_label_counts(label)
    return changed
