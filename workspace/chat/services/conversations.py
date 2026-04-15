from django.db import transaction


def user_conversation_ids(user):
    """Return conversation UUIDs where the user is an active member."""
    from ..models import ConversationMember

    return ConversationMember.objects.filter(
        user=user, left_at__isnull=True,
    ).values_list('conversation_id', flat=True)


def get_active_membership(user, conversation_id):
    """Return the active ConversationMember for *user* in *conversation_id*, or None."""
    from ..models import ConversationMember

    return ConversationMember.objects.filter(
        conversation_id=conversation_id,
        user=user,
        left_at__isnull=True,
    ).first()


@transaction.atomic
def get_or_create_dm(user, other_user):
    """Get or create a DM conversation between two users.

    Deduplicates by finding an existing DM with exactly these two active members.
    If a member had left, reactivates them.
    """
    from ..models import Conversation, ConversationMember

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
    from ..models import ConversationMember

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
