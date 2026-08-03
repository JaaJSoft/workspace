from workspace.common.cache import cached, invalidate_tags
from workspace.core.module_registry import registry
from workspace.core.sse_registry import notify_sse

from ..models import Notification
from ..tasks import send_push_notification

_UNREAD_TTL = 300  # 5 minutes


def _user_tag(user_id):
    return f"notif:user:{user_id}"


# Model label -> Notification FK field. The FK targets are the containers
# users open (conversation, not message): they double as dedup key and
# auto-read trigger.
SOURCE_FIELDS = {
    "chat.conversation": "conversation",
    "files.file": "file",
    "projects.task": "task",
    "calendar.event": "event",
    "calendar.poll": "poll",
}


def source_field(source):
    """FK field name on Notification for *source*; ValueError if unmapped."""
    label = source._meta.label_lower
    try:
        return SOURCE_FIELDS[label]
    except KeyError:
        raise ValueError(
            f"{label} is not a notification source; add it to SOURCE_FIELDS"
        ) from None


def _resolve_module_defaults(origin, icon, color):
    """Fill icon/color from the module registry when not explicitly provided."""
    module = registry.get(origin)
    if module:
        if not icon:
            icon = module.icon
        if not color:
            color = module.color
    return icon, color


def notify(
    *,
    recipient,
    origin,
    icon="",
    title,
    body="",
    url="",
    actor=None,
    priority="normal",
    color="",
    source=None,
):
    """Create a single notification and trigger SSE push."""
    icon, color = _resolve_module_defaults(origin, icon, color)
    source_kwargs = {source_field(source): source} if source is not None else {}
    notif = Notification.objects.create(
        recipient=recipient,
        origin=origin,
        icon=icon,
        color=color,
        title=title,
        body=body,
        url=url,
        actor=actor,
        priority=priority,
        **source_kwargs,
    )
    invalidate_tags(_user_tag(recipient.id))
    notify_sse("notifications", recipient.id)
    if priority != "low":
        send_push_notification.delay(str(notif.uuid))
    return notif


def notify_many(
    *,
    recipients,
    origin,
    icon="",
    title,
    body="",
    url="",
    actor=None,
    priority="normal",
    color="",
    source=None,
):
    """Create notifications for multiple recipients and trigger SSE for each."""
    icon, color = _resolve_module_defaults(origin, icon, color)
    source_kwargs = {source_field(source): source} if source is not None else {}
    notifs = Notification.objects.bulk_create(
        [
            Notification(
                recipient=user,
                origin=origin,
                icon=icon,
                color=color,
                title=title,
                body=body,
                url=url,
                actor=actor,
                priority=priority,
                **source_kwargs,
            )
            for user in recipients
        ]
    )
    for user in recipients:
        invalidate_tags(_user_tag(user.id))
        notify_sse("notifications", user.id)
    if priority != "low":
        for notif in notifs:
            send_push_notification.delay(str(notif.uuid))
    return notifs


@cached(
    key=lambda user: f"notif:unread:{user.pk}",
    ttl=_UNREAD_TTL,
    tags=lambda user: [_user_tag(user.pk)],
)
def get_unread_count(user):
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()
