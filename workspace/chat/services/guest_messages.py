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
    """Drop the reply target of any message answering a pre-floor message.

    The HTML half of what ``GuestMessageSerializer`` does to ``reply_to``: the
    listing is floored at the guest's occurrence, but an in-window reply can
    legitimately answer a message from before that occurrence opened, and the
    quote would hand the guest its author and body. Detaching the relation in
    memory is what the message-group template reads as "no quote"; these rows
    are never saved.
    """
    for message in messages:
        target = message.reply_to if message.reply_to_id else None
        if target is not None and target.created_at < floor:
            message.reply_to = None
