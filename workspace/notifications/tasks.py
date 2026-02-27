import logging

import orjson

from celery import shared_task
from django.conf import settings
from pywebpush import webpush, WebPushException

from workspace.users.presence_service import is_active

logger = logging.getLogger(__name__)


@shared_task(name='notifications.send_push', ignore_result=True, soft_time_limit=30)
def send_push_notification(notification_uuid: str):
    """Send a Web Push notification to all of the recipient's subscriptions."""
    private_key = getattr(settings, 'WEBPUSH_VAPID_PRIVATE_KEY', '')
    if not private_key:
        return

    from workspace.notifications.models import Notification, PushSubscription
    try:
        notif = Notification.objects.select_related('recipient').get(uuid=notification_uuid)
    except Notification.DoesNotExist:
        return

    if is_active(notif.recipient_id):
        return

    subscriptions = PushSubscription.objects.filter(user=notif.recipient)
    if not subscriptions.exists():
        return

    payload = orjson.dumps({
        'title': notif.title,
        'body': notif.body,
        'icon': notif.icon,
        'url': notif.url,
        'origin': notif.origin,
    })

    vapid_claims = getattr(settings, 'WEBPUSH_VAPID_CLAIMS', {})

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    'endpoint': sub.endpoint,
                    'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
                },
                data=payload,
                vapid_private_key=private_key,
                vapid_claims=vapid_claims,
            )
        except WebPushException as e:
            status_code = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            if status_code in (404, 410):
                sub.delete()
                logger.info("Deleted expired push subscription %s", sub.endpoint[:60])
            else:
                logger.warning("Push failed for %s: %s", sub.endpoint[:60], e)
        except Exception:
            logger.exception("Unexpected error sending push to %s", sub.endpoint[:60])
