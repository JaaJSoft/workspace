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
from itertools import batched

from django.db import IntegrityError, transaction
from django.utils import timezone

from workspace.common.logging import scrub

from .counts import (
    refresh_folder_counts,
    refresh_folders_counts_bulk,
    refresh_label_counts,
    refresh_message_label_counts,
)

logger = logging.getLogger(__name__)

# Well under SQLite's 32766 bound parameters per query.
PK_BATCH_SIZE = 10_000


def flag_operations():
    """``{name: (imap_call, field, value)}`` for every flag a message carries.

    Exposed so the bulk path can drive its own loop off the same table
    instead of restating which IMAP call goes with which column.
    """
    from .imap_messages import mark_read, mark_unread, star_message, unstar_message

    return {
        "read": (mark_read, "is_read", True),
        "unread": (mark_unread, "is_read", False),
        "starred": (star_message, "is_starred", True),
        "unstarred": (unstar_message, "is_starred", False),
    }


FLAG_NAMES = ("read", "unread", "starred", "unstarred")


def set_flag(message, flag):
    """Apply one flag to `message` locally and mirror it to IMAP.

    Returns True when the server took it, False when only the local row
    moved. The local write happens either way, on purpose: the mail UI is
    optimistic here and the next sync reconciles. A caller that wants to
    tell the user about the drift reads the return value.
    """
    remote, field, value = flag_operations()[flag]
    synced = True
    setattr(message, field, value)
    try:
        remote(message.account, message)
    except Exception:
        synced = False

    with transaction.atomic():
        message.save(update_fields=[field, "updated_at"])
        refresh_folder_counts(message.folder)
        if field == "is_read":
            refresh_message_label_counts(message)
    return synced


def mark_folder_read(folder):
    """Mark every unread message of `folder`'s merge group read, here and on IMAP.

    Returns ``(updated, synced)``: how many rows turned read, and whether
    every mailbox of the group took the flags. Same contract as `set_flag`:
    the server is asked first, the local write happens either way, and a
    mailbox that refused the flags flips its messages back to unread on the
    next sync.
    """
    from ..models import MailFolder, MailMessage
    from ..queries import folder_group_ids
    from .imap_messages import mark_uids_read
    from .label_counts import refresh_labels_for_messages

    group_ids = folder_group_ids(folder)
    unread = MailMessage.objects.filter(
        folder_id__in=group_ids,
        is_read=False,
        deleted_at__isnull=True,
    )
    affected_ids = []
    uids_by_folder = defaultdict(list)
    for pk, folder_id, uid in unread.values_list("pk", "folder_id", "imap_uid"):
        affected_ids.append(pk)
        uids_by_folder[folder_id].append(uid)

    synced = True
    for mailbox in MailFolder.objects.filter(uuid__in=uids_by_folder):
        try:
            mark_uids_read(folder.account, mailbox, uids_by_folder[mailbox.pk])
        except Exception as e:
            synced = False
            logger.warning(
                "IMAP mark-all-read failed for folder %s: %s",
                scrub(mailbox.name),
                scrub(e),
            )

    # Only the rows whose UIDs went out, and only while they are still where
    # the STORE found them: a message the sync imported during the IMAP calls,
    # or one moved out of the group meanwhile, never got its \Seen, and the
    # next sync would flip it back to unread.
    updated = 0
    now = timezone.now()
    with transaction.atomic():
        for batch in batched(affected_ids, PK_BATCH_SIZE, strict=False):
            updated += MailMessage.objects.filter(
                pk__in=batch,
                folder_id__in=group_ids,
                deleted_at__isnull=True,
                is_read=False,
            ).update(is_read=True, updated_at=now)
            refresh_labels_for_messages(batch)
        refresh_folders_counts_bulk(group_ids)
    return updated, synced


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
            refresh_message_label_counts(message)
        if refresh:
            refresh_folders_counts_bulk({source_id, target.pk})
    return source_id


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
