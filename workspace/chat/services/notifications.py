from workspace.core.sse_registry import notify_sse


def notify_conversation_members(conversation, exclude_user=None):
    """Update SSE cache keys for all active members of a conversation."""
    from ..models import ConversationMember

    member_user_ids = ConversationMember.objects.filter(
        conversation=conversation,
        left_at__isnull=True,
    ).values_list("user_id", flat=True)

    for uid in member_user_ids:
        if exclude_user and uid == exclude_user.id:
            continue
        notify_sse("chat", uid)


def notification_title(conversation, author_name):
    """Notification title for a message from *author_name* in *conversation*.

    ``Alice in Team``, collapsed to ``Alice`` when the conversation label
    adds nothing: DMs and untitled groups carry no title, and a title that
    already reads as the author's name would repeat it.
    """
    label = (conversation.title or "").strip()
    if not label or label.casefold() == author_name.casefold():
        return author_name
    return f"{author_name} in {label}"


def notify_new_message(
    conversation,
    author,
    body,
    mentioned_user_ids=None,
    mention_everyone=False,
    thread_recipient_ids=None,
):
    """Send notifications for a new chat message.

    For a thread reply, *thread_recipient_ids* narrows the candidate pool to
    the thread's participants. A mention is always added on top of that pool:
    being named in a thread you do not follow is exactly the case where the
    notification matters most.

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
    title = notification_title(conversation, author_name)

    if mention_everyone:
        priority_map = dict.fromkeys(member_ids, "high")
    else:
        priority_map = {uid: "high" for uid in member_ids if uid in mentioned_user_ids}

    notify_stream(
        recipient_ids=member_ids,
        source=conversation,
        origin="chat",
        title=title,
        body=preview,
        url=f"/chat/{conversation.pk}",
        actor=author,
        priority_map=priority_map,
    )


def notify_user(user_id):
    """Mark that a user has pending SSE events."""
    notify_sse("chat", user_id)
