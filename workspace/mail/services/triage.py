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

from django.db import transaction
from django.utils import timezone

from workspace.common.logging import scrub

from .counts import (
    refresh_folder_counts,
    refresh_folders_counts_bulk,
    refresh_label_counts,
    refresh_message_label_counts,
)

logger = logging.getLogger(__name__)


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

    with transaction.atomic():
        updated = unread.update(is_read=True)
        MailFolder.objects.filter(uuid__in=group_ids).update(
            unread_count=0, updated_at=timezone.now()
        )
        refresh_labels_for_messages(affected_ids)
    return updated, synced


def move_to_folder(message, target, refresh=True):
    """Move `message` into `target` on the server, then re-point the row.

    Raises whatever the IMAP layer raises, having written nothing locally.
    That order is the invariant: re-pointing the row while the server still
    holds the message in its old folder gives the next sync a message it
    cannot find where we claim it is, and it soft-deletes it.

    Returns the folder the message came from. `refresh=False` leaves the
    counters to a caller batching several moves into one aggregate.
    """
    from .imap_messages import move_message

    move_message(message.account, message, target)

    source_id = message.folder_id
    message.folder = target
    with transaction.atomic():
        message.save(update_fields=["folder", "updated_at"])
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
