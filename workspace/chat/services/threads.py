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

    Locks the participant row: read-modify-write on the counter, so two
    concurrent reads of the same thread would otherwise both see the full
    backlog and both subtract it from the badge. Callers must therefore run
    this inside a transaction. (SQLite ignores the lock, having one writer.)
    """
    participant = (
        ThreadParticipant.objects.select_for_update()
        .filter(root_message=root, user=user)
        .first()
    )
    if participant is None:
        return 0
    cleared = max(0, participant.unread_count)
    participant.unread_count = 0
    participant.last_read_at = timezone.now()
    participant.save(update_fields=["unread_count", "last_read_at"])
    return cleared


def recount_thread(root):
    """Recompute *root*'s denormalised counters from its live replies.

    Called after a reply is soft-deleted. Derived rather than decremented: a
    decrement would also have to know whether the deleted row was the latest
    reply, and would drift from the truth the moment any other path touched a
    message.
    """
    from django.db.models import Count, Max

    from ..models import Message

    stats = Message.objects.filter(thread_root=root, deleted_at__isnull=True).aggregate(
        total=Count("uuid"),
        latest=Max("created_at"),
    )
    Message.objects.filter(pk=root.pk).update(
        reply_count=stats["total"],
        last_reply_at=stats["latest"],
    )


def show_thread_replies_inline(user):
    """Whether *user* wants thread replies repeated in the main flow.

    Read server-side and applied as a queryset filter, not hidden client-side:
    the message list is cursor-paginated 50 at a time, and dropping rows after
    the fact would yield half-empty pages and a wrong "load older" boundary.
    """
    from workspace.users.services.settings import get_setting

    preferences = get_setting(user, "chat", "preferences", default=None) or {}
    return bool(preferences.get("showThreadRepliesInline"))


def backfill_threads(message_model, participant_model, member_model):
    """Turn historical `reply_to` chains into threads.

    Called by migration 0028_backfill_thread_roots, which passes the historical
    models from the app registry. Its signature therefore has to stay
    compatible, and the body must keep working against historical model
    classes: no model methods, no properties, only fields and the manager.

    Takes its models as arguments so the data migration can pass the historical
    versions from the app registry while the tests pass the real ones.
    Idempotent: re-running it recomputes the same counters and adds no
    duplicate participants.

    A chain that loops (possible only from corrupted legacy data) is left
    unthreaded rather than picked arbitrarily: an unthreaded message renders
    exactly as it does today, which is the safe failure.
    """
    parents = dict(
        message_model.objects.filter(reply_to__isnull=False).values_list(
            "uuid", "reply_to_id"
        )
    )

    roots = {}
    for uuid in parents:
        seen = {uuid}
        current = uuid
        while current in parents:
            current = parents[current]
            if current in seen:
                current = None
                break
            seen.add(current)
        if current is not None and current != uuid:
            roots[uuid] = current

    if not roots:
        return

    replies = list(
        message_model.objects.filter(uuid__in=roots).only(
            "uuid", "author_id", "created_at", "conversation_id"
        )
    )
    for reply in replies:
        reply.thread_root_id = roots[reply.uuid]
    message_model.objects.bulk_update(replies, ["thread_root"], batch_size=500)

    by_root = {}
    for reply in replies:
        by_root.setdefault(reply.thread_root_id, []).append(reply)

    roots_meta = {
        uuid: (author_id, conversation_id)
        for uuid, author_id, conversation_id in message_model.objects.filter(
            uuid__in=by_root
        ).values_list("uuid", "author_id", "conversation_id")
    }

    root_rows = list(message_model.objects.filter(uuid__in=by_root).only("uuid"))
    for root in root_rows:
        group = by_root[root.uuid]
        root.reply_count = len(group)
        root.last_reply_at = max(r.created_at for r in group)
    message_model.objects.bulk_update(
        root_rows, ["reply_count", "last_reply_at"], batch_size=500
    )

    read_marks = {
        (conversation_id, user_id): last_read_at
        for conversation_id, user_id, last_read_at in member_model.objects.filter(
            conversation_id__in={c for _, c in roots_meta.values()}
        ).values_list("conversation_id", "user_id", "last_read_at")
    }

    existing = {
        (root_id, user_id)
        for root_id, user_id in participant_model.objects.filter(
            root_message_id__in=by_root
        ).values_list("root_message_id", "user_id")
    }

    to_create = []
    for root_id, group in by_root.items():
        root_author_id, conversation_id = roots_meta[root_id]
        user_ids = {root_author_id} | {r.author_id for r in group}
        for user_id in user_ids:
            if (root_id, user_id) in existing:
                continue
            to_create.append(
                participant_model(
                    root_message_id=root_id,
                    user_id=user_id,
                    last_read_at=read_marks.get((conversation_id, user_id)),
                )
            )
    participant_model.objects.bulk_create(
        to_create, batch_size=500, ignore_conflicts=True
    )
