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

from ..models import Conversation, ConversationMember, Message, ThreadParticipant
from .notifications import notify_conversation_members, notify_new_message
from .threads import ensure_participants, participant_user_ids


def _thread_delivery(message, author):
    """Counters for a reply: the thread's participants, and nobody else.

    Returns the participant ids so the notification fan-out targets the same
    set the badges moved for. A member of the conversation who is not in the
    thread sees only the reply count on the root message change.
    """
    root = message.thread_root
    ensure_participants(root, [root.author_id, author.id])
    if message.mentioned_user_ids:
        ensure_participants(root, message.mentioned_user_ids)

    recipient_ids = participant_user_ids(root) - {author.id}

    ThreadParticipant.objects.filter(
        root_message=root, user_id__in=recipient_ids
    ).update(unread_count=F("unread_count") + 1)

    ConversationMember.objects.filter(
        conversation_id=message.conversation_id,
        left_at__isnull=True,
        user_id__in=recipient_ids,
    ).update(unread_count=F("unread_count") + 1)

    Message.objects.filter(pk=root.pk).update(
        reply_count=F("reply_count") + 1,
        last_reply_at=timezone.now(),
    )
    return recipient_ids


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

    Both callbacks are robust, and both halves need it: a non-robust callback
    that raises propagates out of Django's commit loop, which drops every
    callback still queued behind it — robust ones included. The transaction is
    already committed at that point, so the message would exist while one of
    its two fan-outs never ran.
    """
    message.mentioned_user_ids = mentioned_user_ids or set()
    author = message.author

    if message.thread_root_id:
        recipient_ids = _thread_delivery(message, author)
    else:
        recipient_ids = None
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
        lambda: notify_conversation_members(conversation, exclude_user=author),
        robust=True,
    )
    transaction.on_commit(
        lambda: notify_new_message(
            conversation,
            author,
            message.body,
            mentioned_user_ids=mentioned_user_ids,
            mention_everyone=mention_everyone,
            thread_recipient_ids=recipient_ids,
        ),
        robust=True,
    )
