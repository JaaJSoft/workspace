"""The side effects a message owes its conversation once it exists.

A posted message is more than its row: the other members' unread counters
move, the conversation rises in the list, open tabs are told to refresh, and
the bell and push pipeline runs. Every entry point used to wire that set by
hand, and each one that got it wrong got it wrong silently — the author's own
client rendered fine either way.
"""

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ..models import Conversation, ConversationMember
from .notifications import notify_conversation_members, notify_new_message


def deliver_message(
    conversation,
    message,
    *,
    mentioned_user_ids=None,
    mention_everyone=False,
):
    """Apply every side effect of *message* landing in *conversation*.

    Call this from inside the transaction that created the message. The
    counters then land with the row, and the fan-out is deferred to commit so
    no client is ever told about a message it cannot read yet.

    The notification fan-out runs as a robust callback of its own: a broker
    error while dispatching the push must not take the SSE refresh with it.
    """
    author = message.author

    ConversationMember.objects.filter(
        conversation_id=conversation.pk,
        left_at__isnull=True,
    ).exclude(user=author).update(
        unread_count=F("unread_count") + 1,
    )

    Conversation.objects.filter(pk=conversation.pk).update(
        updated_at=timezone.now(),
    )

    transaction.on_commit(
        lambda: notify_conversation_members(conversation, exclude_user=author)
    )
    transaction.on_commit(
        lambda: notify_new_message(
            conversation,
            author,
            message.body,
            mentioned_user_ids=mentioned_user_ids,
            mention_everyone=mention_everyone,
        ),
        robust=True,
    )
