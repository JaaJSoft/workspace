from django.core.cache import cache

from workspace.core.module_registry import registry
from workspace.core.sse_registry import notify_sse
from .models import Notification
from .tasks import send_push_notification

_UNREAD_KEY = 'notif:unread:{}'
_UNREAD_TTL = 300  # 5 minutes


def _resolve_module_defaults(origin, icon, color):
    """Fill icon/color from the module registry when not explicitly provided."""
    module = registry.get(origin)
    if module:
        if not icon:
            icon = module.icon
        if not color:
            color = module.color
    return icon, color


def _invalidate_unread(user_id):
    cache.delete(_UNREAD_KEY.format(user_id))


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
    _invalidate_unread(recipient.id)
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
        _invalidate_unread(user.id)
        notify_sse('notifications', user.id)
    if priority != 'low':
        for notif in notifs:
            send_push_notification.delay(str(notif.uuid))
    return notifs


def get_unread_count(user):
    key = _UNREAD_KEY.format(user.pk)
    count = cache.get(key)
    if count is None:
        count = Notification.objects.filter(recipient=user, read_at__isnull=True).count()
        cache.set(key, count, _UNREAD_TTL)
    return count
