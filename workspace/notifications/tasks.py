import logging

import orjson
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from pywebpush import WebPushException, webpush

from workspace.common.logging import scrub
from workspace.users.services.presence import is_active

logger = logging.getLogger(__name__)

PUSH_COOLDOWN_SECONDS = 60
RETENTION_DAYS = 90
_SOURCE_ID_ATTRS = (
    "conversation_id",
    "file_id",
    "task_id",
    "event_id",
    "poll_id",
)


def _source_cooldown_key(notif):
    for attr in _SOURCE_ID_ATTRS:
        value = getattr(notif, attr)
        if value:
            return f"push:cd:{notif.recipient_id}:{attr}:{value}"
    return None


@shared_task(name="notifications.send_push", ignore_result=True, soft_time_limit=30)
def send_push_notification(notification_uuid: str):
    """Send a Web Push notification to all of the recipient's subscriptions."""
    private_key = getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "")
    if not private_key:
        logger.warning("Push skipped: WEBPUSH_VAPID_PRIVATE_KEY is not configured")
        return

    from workspace.notifications.models import Notification, PushSubscription

    try:
        notif = Notification.objects.select_related("recipient").get(
            uuid=notification_uuid
        )
    except Notification.DoesNotExist:
        return

    if notif.read_at is not None:
        # The user saw it before the worker got here.
        return

    if is_active(notif.recipient_id):
        logger.debug("Skipping push for %s: user is active", notif.recipient_id)
        return

    if notif.priority != "urgent":
        cooldown_key = _source_cooldown_key(notif)
        # cache.add is an atomic SET NX on Redis; if the key is already there
        # a push for this (user, source) went out within the window. Fail-open:
        # Redis trouble means pushes send rather than drop.
        if cooldown_key and not cache.add(cooldown_key, 1, PUSH_COOLDOWN_SECONDS):
            logger.debug("Push cooldown hit for %s", notif.recipient_id)
            return

    subscriptions = PushSubscription.objects.filter(user=notif.recipient)
    if not subscriptions.exists():
        return

    payload = orjson.dumps(
        {
            "title": notif.title,
            "body": notif.body,
            "icon": notif.icon,
            "url": notif.url,
            "origin": notif.origin,
        }
    )

    vapid_claims = getattr(settings, "WEBPUSH_VAPID_CLAIMS", {})
    if not vapid_claims.get("sub"):
        logger.warning(
            "Push skipped: WEBPUSH_VAPID_MAILTO is not configured (vapid_claims.sub is empty)"
        )
        return

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=private_key,
                vapid_claims=vapid_claims,
            )
        except WebPushException as e:
            status_code = (
                getattr(e.response, "status_code", None)
                if hasattr(e, "response")
                else None
            )
            if status_code in (404, 410):
                sub.delete()
                logger.info(
                    "Deleted expired push subscription %s", scrub(sub.endpoint[:60])
                )
            else:
                logger.warning("Push failed for %s: %s", scrub(sub.endpoint[:60]), e)
        except Exception:
            logger.exception(
                "Unexpected error sending push to %s", scrub(sub.endpoint[:60])
            )


@shared_task(name="notifications.prune_read", ignore_result=True)
def prune_read_notifications():
    """Delete read notifications older than RETENTION_DAYS. Unread rows are
    kept forever: pruning something the user never saw is data loss."""
    from datetime import timedelta

    from django.utils import timezone

    from workspace.notifications.models import Notification

    cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
    deleted, _ = Notification.objects.filter(read_at__lt=cutoff).delete()
    if deleted:
        logger.info("Pruned %d read notifications", deleted)
    return deleted
