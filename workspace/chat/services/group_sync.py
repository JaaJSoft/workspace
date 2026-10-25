from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from .conversations import ensure_group_admin, lock_conversation
from .threads import mark_conversation_threads_read


@transaction.atomic
def resync_conversation_members(conversation):
    """Reconcile member rows with the union of the attached groups' active users.

    Idempotent: creates missing rows, reactivates previously-left covered users
    (history preserved, unread reset), soft-deactivates rows no longer covered
    by any attached group, and promotes an admin if those took the last one.
    """
    from ..models import ConversationMember

    lock_conversation(conversation.pk)

    # A bot never belongs to a group conversation (can_join_group): a bot
    # user in an attached group is not covered, so an existing bot row is
    # deactivated rather than kept.
    covered = set(
        User.objects.filter(
            groups__in=conversation.groups.all(),
            is_active=True,
            bot_profile__isnull=True,
        )
        .values_list("id", flat=True)
        .distinct()
    )
    # Query directly instead of conversation.members.all(): a caller holding a
    # filtered members prefetch (active-only) would otherwise hide left rows
    # and bulk_create would hit the unique constraint on reactivation.
    existing = {
        m.user_id: m
        for m in ConversationMember.objects.filter(conversation=conversation)
    }

    to_create = [
        ConversationMember(conversation=conversation, user_id=uid)
        for uid in covered - existing.keys()
    ]
    if to_create:
        ConversationMember.objects.bulk_create(to_create)

    to_reactivate = [
        m for uid, m in existing.items() if uid in covered and m.left_at is not None
    ]
    for m in to_reactivate:
        m.left_at = None
        m.unread_count = 0
    if to_reactivate:
        ConversationMember.objects.bulk_update(
            to_reactivate, ["left_at", "unread_count"]
        )
        # The badge restarts at zero, so the thread counters it used to
        # contain must restart with it.
        mark_conversation_threads_read(
            [m.user_id for m in to_reactivate], conversation.pk
        )

    now = timezone.now()
    to_deactivate = [
        m for uid, m in existing.items() if uid not in covered and m.left_at is None
    ]
    for m in to_deactivate:
        m.left_at = now
        m.role = ConversationMember.Role.MEMBER
    if to_deactivate:
        ConversationMember.objects.bulk_update(to_deactivate, ["left_at", "role"])
        ensure_group_admin(conversation.pk)


def is_group_linked(conversation_id):
    """Whether the conversation has attached auth groups (membership is synced)."""
    from ..models import Conversation

    return Conversation.objects.filter(
        pk=conversation_id, groups__isnull=False
    ).exists()


@transaction.atomic
def create_group_conversation(user, groups, title=""):
    """Create a conversation whose membership follows *groups* (union).

    The creator must belong to at least one group; attaching further groups
    they are not in is allowed (cross-team channels). They are its first admin.
    """
    from ..models import Conversation, ConversationMember

    groups = list(groups)
    if not user.groups.filter(pk__in=[g.pk for g in groups]).exists():
        raise PermissionDenied("You must belong to at least one of the groups.")

    conversation = Conversation.objects.create(
        kind=Conversation.Kind.GROUP,
        title=(title or "").strip() or groups[0].name,
        created_by=user,
    )
    ConversationMember.objects.create(
        conversation=conversation, user=user, role=ConversationMember.Role.ADMIN
    )
    conversation.groups.set(groups)
    resync_conversation_members(conversation)
    return conversation
