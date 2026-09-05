"""Shared read-path plumbing for a meeting guest's messages.

Both the guest REST endpoints (``views/meeting_guest.py``) and the guest SSE
stream (``guest_stream.py``) read the same rows through the same
redaction-relevant relations - MESSAGE_SELECT_RELATED lives here, in a module
neither of those two owns, so the two paths cannot drift apart on what they
hydrate.
"""

from django.db.models import Prefetch

from ..models import Message, Reaction

MESSAGE_SELECT_RELATED = (
    "author",
    "author__bot_profile",
    "guest",
    "reply_to",
    "reply_to__author",
    "reply_to__guest",
    "thread_root",
    "interaction",
    "interaction__interacted_by",
)


def message_queryset():
    """Base queryset for a guest-visible message, fully hydrated for redaction.

    No ``deleted_at`` filter: by the time a soft-deleted row could be read
    here, it is a purged tombstone - ``purge_message_content`` blanks its
    body and strips its attachments in the same request that sets
    ``deleted_at`` - so there is nothing left to leak. That guarantee lives
    there, not in this queryset or in the serializer that reads it.
    """
    return Message.objects.select_related(*MESSAGE_SELECT_RELATED).prefetch_related(
        Prefetch("reactions", queryset=Reaction.objects.select_related("user")),
        "attachments",
        "link_previews__preview",
    )


def hide_quotes_below_floor(messages, floor):
    """Redact whatever a reply quote reaches below the guest's floor.

    The HTML half of what ``GuestMessageSerializer`` does to ``reply_to`` and
    ``thread_root``, and it has to redact both for the same reason the
    serializer does. The listing is floored at the guest's occurrence, but an
    in-window reply can legitimately answer a message from before that
    occurrence opened, and the quote would hand the guest its author and
    body. A surviving quote is worse than it looks: its target may itself sit
    in a thread whose root is pre-floor, and the root rides along on the
    quote as ``data-reply-thread-root`` - a uuid the guest could then name as
    ``reply_to_uuid`` on a POST, which is a pull primitive around the floor.

    Detaching the relations in memory is what the message-group template
    reads as "no quote" and "no root"; these rows are never saved.
    """
    surviving_quotes = []
    for message in messages:
        target = message.reply_to if message.reply_to_id else None
        if target is None:
            continue
        if target.created_at < floor:
            message.reply_to = None
        else:
            surviving_quotes.append(target)

    # One query for the whole page, and only when a surviving quote points
    # into a thread: reply_to__thread_root is not select_related, so reading
    # each root through the relation would cost a query per quote.
    root_ids = {q.thread_root_id for q in surviving_quotes if q.thread_root_id}
    if not root_ids:
        return
    buried = set(
        Message.objects.filter(uuid__in=root_ids, created_at__lt=floor).values_list(
            "uuid", flat=True
        )
    )
    for quote in surviving_quotes:
        if quote.thread_root_id in buried:
            quote.thread_root = None
