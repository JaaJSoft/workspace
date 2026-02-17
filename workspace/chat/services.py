from django.core.cache import cache
from django.db.models import Count, F, Q
from django.utils import timezone

import mistune


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

    memberships = (
        ConversationMember.objects.filter(
            user=user,
            left_at__isnull=True,
        )
        .annotate(
            unread_count=Count(
                'conversation__messages',
                filter=Q(
                    conversation__messages__deleted_at__isnull=True,
                    conversation__messages__created_at__gt=F('last_read_at'),
                ) & ~Q(conversation__messages__author=user),
            )
        )
        .filter(unread_count__gt=0)
    )

    # Also count messages where last_read_at is NULL (never read)
    null_memberships = (
        ConversationMember.objects.filter(
            user=user,
            left_at__isnull=True,
            last_read_at__isnull=True,
        )
        .annotate(
            unread_count=Count(
                'conversation__messages',
                filter=Q(
                    conversation__messages__deleted_at__isnull=True,
                ) & ~Q(conversation__messages__author=user),
            )
        )
        .filter(unread_count__gt=0)
    )

    conversations = {}
    total = 0

    for m in memberships:
        conversations[str(m.conversation_id)] = m.unread_count
        total += m.unread_count

    for m in null_memberships:
        conv_id = str(m.conversation_id)
        if conv_id not in conversations:
            conversations[conv_id] = m.unread_count
            total += m.unread_count

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

    now = timezone.now().isoformat()
    for uid in member_user_ids:
        if exclude_user and uid == exclude_user.id:
            continue
        cache.set(f'sse:chat:last_event:{uid}', now, 120)


def notify_user(user_id):
    """Mark that a user has pending SSE events."""
    cache.set(f'sse:chat:last_event:{user_id}', timezone.now().isoformat(), 120)
