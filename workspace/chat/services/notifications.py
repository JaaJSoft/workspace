from workspace.core.sse_registry import notify_sse


def notify_conversation_members(conversation, exclude_user=None):
    """Update SSE cache keys for all active members of a conversation."""
    from ..models import ConversationMember

    member_user_ids = ConversationMember.objects.filter(
        conversation=conversation,
        left_at__isnull=True,
    ).values_list('user_id', flat=True)

    for uid in member_user_ids:
        if exclude_user and uid == exclude_user.id:
            continue
        notify_sse('chat', uid)


def notify_new_message(conversation, author, body, mentioned_user_ids=None, mention_everyone=False):
    """Send push notifications for a new chat message.

    Merges into existing unread notifications for the same conversation:
    - First message: creates a new notification + sends push
    - Subsequent messages within 60s: updates the existing notification body/title
      (no duplicate push)
    """
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from workspace.notifications.models import Notification
    from workspace.notifications.services.notifications import _resolve_module_defaults
    from workspace.notifications.tasks import send_push_notification
    from workspace.core.sse_registry import notify_sse as _notify_sse
    from ..models import ConversationMember

    mentioned_user_ids = mentioned_user_ids or set()

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

    # Batch-fetch existing unread chat notifications for all members at once.
    # Previously this was a SELECT per member (N+1); a 50-member conversation
    # cost 50 round-trips before we'd even decide update-vs-create.
    existing_notifs = {
        n.recipient_id: n
        for n in Notification.objects.filter(
            recipient_id__in=member_ids,
            origin='chat',
            url=conv_url,
            read_at__isnull=True,
        )
    }

    now = timezone.now()
    to_update = []
    to_create = []
    for uid in member_ids:
        is_mentioned = uid in mentioned_user_ids or mention_everyone
        priority = 'high' if is_mentioned else 'normal'

        existing = existing_notifs.get(uid)
        if existing:
            # Merge: update body/title, bump timestamp so it rises in the list.
            existing.body = preview
            existing.title = title_single
            existing.actor = author
            if is_mentioned and existing.priority != 'urgent':
                existing.priority = priority
            existing.created_at = now
            to_update.append(existing)
        else:
            # First message: will be created below and triggers a push.
            to_create.append(Notification(
                recipient_id=uid,
                origin='chat',
                icon=icon,
                color=color,
                title=title_single,
                body=preview,
                url=conv_url,
                actor=author,
                priority=priority,
            ))

    # Collapse N saves + N created_at bumps into 2 statements total.
    # Notification has auto_now_add on created_at (not auto_now), so setting
    # it manually on the update path is safe — it only fires on INSERT.
    if to_update:
        Notification.objects.bulk_update(
            to_update, ['body', 'title', 'actor', 'priority', 'created_at'],
        )
    if to_create:
        # uuid_v7_or_v4 default runs at Notification() __init__, so every
        # instance already has a pk before bulk_create — we can dispatch
        # the push task straight from the Python objects.
        Notification.objects.bulk_create(to_create)

    # SSE ping refreshes the bell icon for every recipient regardless of
    # update/create path. Push notifications only fire for fresh entries.
    for uid in member_ids:
        _notify_sse('notifications', uid)
    for notif in to_create:
        send_push_notification.delay(str(notif.uuid))


def notify_user(user_id):
    """Mark that a user has pending SSE events."""
    notify_sse('chat', user_id)
