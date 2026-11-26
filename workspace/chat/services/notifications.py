from collections import defaultdict

from django.db import transaction
from django.utils import translation
from django.utils.translation import gettext

from workspace.core.sse_registry import notify_sse, push_user_event
from workspace.users.services.settings import get_user_languages

SSE_SLUG = "chat"


def _active_member_ids(conversation, exclude_user=None):
    from ..models import ConversationMember

    member_ids = ConversationMember.objects.filter(
        conversation=conversation,
        left_at__isnull=True,
    ).values_list("user_id", flat=True)
    if exclude_user is not None:
        member_ids = member_ids.exclude(user_id=exclude_user.id)
    return member_ids


def notify_conversation_members(conversation, exclude_user=None, *, dirty=True):
    """Wake the chat stream of every active member of a conversation.

    ``dirty`` as in ``notify_sse``: False when nothing changed in the database.
    """
    for uid in _active_member_ids(conversation, exclude_user):
        notify_sse(SSE_SLUG, uid, dirty=dirty)


def push_conversation_event(conversation, payload, exclude_user=None):
    """Queue *payload* on the chat stream of every active member, and wake it.

    For a change the stream cannot find by polling the database: a row that
    was deleted leaves nothing newer than its cursor. Sent once the write
    commits, so a member refreshing on the event reads the row already gone.
    """

    def push():
        for uid in _active_member_ids(conversation, exclude_user):
            push_user_event(SSE_SLUG, uid, payload)

    # A failed push must not turn a write that committed into an error.
    transaction.on_commit(push, robust=True)


def notification_title(conversation, author_name):
    """Notification title for a message from *author_name* in *conversation*.

    ``Alice in Team``, collapsed to ``Alice`` when the conversation label
    adds nothing: DMs and untitled groups carry no title, and a title that
    already reads as the author's name would repeat it.
    """
    label = (conversation.title or "").strip()
    if not label or label.casefold() == author_name.casefold():
        return author_name
    # Translators: notification title, "Alice in Team".
    return gettext("%(author)s in %(conversation)s") % {
        "author": author_name,
        "conversation": label,
    }


def notify_new_message(
    conversation,
    author,
    body,
    mentioned_user_ids=None,
    mention_everyone=False,
    thread_recipient_ids=None,
    thread_root_id=None,
):
    """Send notifications for a new chat message.

    For a thread reply, *thread_recipient_ids* narrows the candidate pool to
    the thread's participants. A mention is always added on top of that pool:
    being named in a thread you do not follow is exactly the case where the
    notification matters most. Deliberately, ``@everyone`` does not widen the
    pool back to the whole conversation - inside a thread it raises the
    priority for the thread's participants (and anyone explicitly mentioned),
    nobody else.

    *thread_root_id* deep-links the notification to the thread panel: by
    default a reply is not in the main flow, so landing on the bare
    conversation would show nothing new.

    Storage semantics (merge into the recipient's unread notification for
    this conversation, push only on fresh rows) live in notify_stream.
    """
    from workspace.notifications.services.notifications import notify_stream

    from ..models import ConversationMember

    mentioned_user_ids = mentioned_user_ids or set()

    # Bots are ordinary members but never read a notification, so a row
    # created for one is never marked read and never pruned.
    members = ConversationMember.objects.filter(
        conversation=conversation,
        left_at__isnull=True,
        user__bot_profile__isnull=True,
    ).exclude(user=author)

    if thread_recipient_ids is not None:
        members = members.filter(
            user_id__in=set(thread_recipient_ids) | set(mentioned_user_ids)
        )

    levels = ConversationMember.NotificationLevel
    member_ids = [
        uid
        for uid, level in members.values_list("user_id", "notification_level")
        if level == levels.ALL
        or (
            level == levels.MENTIONS and (mention_everyone or uid in mentioned_user_ids)
        )
    ]
    if not member_ids:
        return

    author_name = author.get_full_name() or author.username
    preview = (body[:150] + "...") if len(body) > 150 else body

    if mention_everyone:
        priority_map = dict.fromkeys(member_ids, "high")
    else:
        priority_map = {uid: "high" for uid in member_ids if uid in mentioned_user_ids}

    url = f"/chat/{conversation.pk}"
    if thread_root_id:
        url = f"{url}?thread={thread_root_id}"

    # The title is stored once per recipient, so each one gets it written in
    # the language they read rather than the author's.
    by_language = defaultdict(list)
    for uid, language in get_user_languages(member_ids).items():
        by_language[language].append(uid)
    for language, recipient_ids in by_language.items():
        with translation.override(language):
            notify_stream(
                recipient_ids=recipient_ids,
                source=conversation,
                origin="chat",
                title=notification_title(conversation, author_name),
                body=preview,
                url=url,
                actor=author,
                priority_map=priority_map,
            )
