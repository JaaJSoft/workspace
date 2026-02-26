from workspace.core.module_registry import registry
from workspace.core.sse_registry import notify_sse
from .models import Notification
from .tasks import send_push_notification


def _resolve_module_defaults(origin, icon, color):
    """Fill icon/color from the module registry when not explicitly provided."""
    module = registry.get(origin)
    if module:
        if not icon:
            icon = module.icon
        if not color:
            color = module.color
    return icon, color


def notify(*, recipient, origin, icon='', title, body='', url='', actor=None, priority='normal', color=''):
    """Create a single notification and trigger SSE push."""
    icon, color = _resolve_module_defaults(origin, icon, color)
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
    )
    notify_sse('notifications', recipient.id)
    if priority != 'low':
        send_push_notification.delay(str(notif.uuid))
    return notif


def notify_many(*, recipients, origin, icon='', title, body='', url='', actor=None, priority='normal', color=''):
    """Create notifications for multiple recipients and trigger SSE for each."""
    icon, color = _resolve_module_defaults(origin, icon, color)
    notifs = Notification.objects.bulk_create([
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
        )
        for user in recipients
    ])
    for user in recipients:
        notify_sse('notifications', user.id)
    if priority != 'low':
        for notif in notifs:
            send_push_notification.delay(str(notif.uuid))
    return notifs


def get_unread_count(user):
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()
