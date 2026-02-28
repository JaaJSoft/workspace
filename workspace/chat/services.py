import mistune

from workspace.core.sse_registry import notify_sse


def get_or_create_dm(user, other_user):
    """Get or create a DM conversation between two users.

    Deduplicates by finding an existing DM with exactly these two active members.
    If a member had left, reactivates them.
    """
    from .models import Conversation, ConversationMember

    user_ids = sorted([user.id, other_user.id])

    # Find existing DM with both users as members
    existing = (
        Conversation.objects.filter(kind=Conversation.Kind.DM)
        .filter(
            members__user_id=user_ids[0],
        )
        .filter(
            members__user_id=user_ids[1],
        )
        .first()
    )

    if existing:
        # Reactivate any member that left
        ConversationMember.objects.filter(
            conversation=existing,
            user_id__in=user_ids,
            left_at__isnull=False,
        ).update(left_at=None)
        return existing

    # Create new DM
    conversation = Conversation.objects.create(
        kind=Conversation.Kind.DM,
        created_by=user,
    )
    ConversationMember.objects.bulk_create([
        ConversationMember(conversation=conversation, user=user),
        ConversationMember(conversation=conversation, user=other_user),
    ])
    return conversation


def get_unread_counts(user):
    """Return unread message counts for each conversation the user is in."""
    from .models import ConversationMember

    memberships = ConversationMember.objects.filter(
        user=user,
        left_at__isnull=True,
        unread_count__gt=0,
    ).values_list('conversation_id', 'unread_count')

    conversations = {}
    total = 0
    for conv_id, count in memberships:
        conversations[str(conv_id)] = count
        total += count

    return {'total': total, 'conversations': conversations}


# Markdown renderer configured for chat (no images, no headings)
_markdown = mistune.create_markdown(
    escape=True,
    plugins=['strikethrough', 'url'],
)


def render_message_body(body):
    """Render markdown body to HTML suitable for chat messages."""
    return _markdown(body)


def notify_conversation_members(conversation, exclude_user=None):
    """Update SSE cache keys for all active members of a conversation."""
    from .models import ConversationMember

    member_user_ids = ConversationMember.objects.filter(
        conversation=conversation,
        left_at__isnull=True,
    ).values_list('user_id', flat=True)

    for uid in member_user_ids:
        if exclude_user and uid == exclude_user.id:
            continue
        notify_sse('chat', uid)


def notify_new_message(conversation, author, body):
    """Send push notifications for a new chat message.

    Merges into existing unread notifications for the same conversation:
    - First message: creates a new notification + sends push
    - Subsequent messages within 60s: updates the existing notification body/title
      (no duplicate push)
    """
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from workspace.notifications.models import Notification
    from workspace.notifications.services import _resolve_module_defaults
    from workspace.notifications.tasks import send_push_notification
    from workspace.core.sse_registry import notify_sse as _notify_sse
    from .models import ConversationMember

    User = get_user_model()
    member_ids = list(
        ConversationMember.objects.filter(
            conversation=conversation,
            left_at__isnull=True,
        ).exclude(user=author).values_list('user_id', flat=True)
    )
    if not member_ids:
        return

    author_name = author.get_full_name() or author.username
    conv_title = conversation.title
    conv_url = f'/chat/{conversation.pk}'
    preview = (body[:150] + '...') if len(body) > 150 else body

    if conv_title:
        title_single = f'{author_name} in {conv_title}'
    else:
        title_single = author_name

    icon, color = _resolve_module_defaults('chat', '', '')

    for uid in member_ids:
        # Try to merge into an existing unread notification for this conversation
        existing = Notification.objects.filter(
            recipient_id=uid,
            origin='chat',
            url=conv_url,
            read_at__isnull=True,
        ).first()

        if existing:
            # Merge: update body/title, bump timestamp
            existing.body = preview
            existing.title = title_single
            existing.actor = author
            existing.save(update_fields=['body', 'title', 'actor'])
            # Bump created_at so it rises to the top of the list
            Notification.objects.filter(pk=existing.pk).update(created_at=timezone.now())
            # Refresh the bell icon count via SSE (count unchanged but content updated)
            _notify_sse('notifications', uid)
        else:
            # First message: create notification + send push
            notif = Notification.objects.create(
                recipient_id=uid,
                origin='chat',
                icon=icon,
                color=color,
                title=title_single,
                body=preview,
                url=conv_url,
                actor=author,
            )
            _notify_sse('notifications', uid)
            send_push_notification.delay(str(notif.uuid))


def notify_user(user_id):
    """Mark that a user has pending SSE events."""
    notify_sse('chat', user_id)
