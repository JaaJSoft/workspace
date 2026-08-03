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


def notify_new_message(
    conversation, author, body, mentioned_user_ids=None, mention_everyone=False
):
    """Send notifications for a new chat message.

    Storage semantics (merge into the recipient's unread notification for
    this conversation, push only on fresh rows) live in notify_stream.
    """
    from workspace.notifications.services.notifications import notify_stream

    from ..models import ConversationMember

    mentioned_user_ids = mentioned_user_ids or set()

    member_ids = list(
        ConversationMember.objects.filter(
            conversation=conversation,
            left_at__isnull=True,
        )
        .exclude(user=author)
        .values_list("user_id", flat=True)
    )
    if not member_ids:
        return

    author_name = author.get_full_name() or author.username
    preview = (body[:150] + "...") if len(body) > 150 else body
    if conversation.title:
        title = f"{author_name} in {conversation.title}"
    else:
        title = author_name

    if mention_everyone:
        priority_map = {uid: "high" for uid in member_ids}
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
