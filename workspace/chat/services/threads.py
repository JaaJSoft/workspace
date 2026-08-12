"""Thread membership: which root a reply belongs to, and who takes part.

A thread is flat. `reply_to` still records the specific message being answered
so the quote block keeps working inside a thread, but `thread_root` is what
decides membership, and it always points at the message that started the whole
thing.
"""

from django.utils import timezone

from ..models import ThreadParticipant


def resolve_thread_root(parent):
    """The root of the thread a reply to *parent* belongs to.

    One hop, not a walk: `thread_root` is maintained on every reply as it is
    created, so a reply's root is already the root of the whole thread. The
    backfill migration establishes the same invariant for historical rows.
    """
    return parent.thread_root or parent


def participant_user_ids(root):
    return set(
        ThreadParticipant.objects.filter(root_message=root).values_list(
            "user_id", flat=True
        )
    )


def ensure_participants(root, user_ids):
    """Subscribe *user_ids* to *root*'s thread, skipping those already in."""
    wanted = {uid for uid in user_ids if uid is not None}
    if not wanted:
        return
    missing = wanted - participant_user_ids(root)
    if not missing:
        return
    ThreadParticipant.objects.bulk_create(
        [ThreadParticipant(root_message=root, user_id=uid) for uid in missing],
        ignore_conflicts=True,
    )


def mark_thread_read(root, user):
    """Clear *user*'s backlog on *root*'s thread, returning how much it was.

    The caller needs the amount to subtract the same number from the
    conversation badge, which counts thread replies only for participants.
    """
    participant = ThreadParticipant.objects.filter(root_message=root, user=user).first()
    if participant is None:
        return 0
    cleared = max(0, participant.unread_count)
    participant.unread_count = 0
    participant.last_read_at = timezone.now()
    participant.save(update_fields=["unread_count", "last_read_at"])
    return cleared
