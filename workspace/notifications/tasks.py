import logging

import orjson
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from pywebpush import WebPushException, webpush

from workspace.common.logging import scrub
from workspace.notifications.services.vapid import VapidKeyError, load_vapid_key
from workspace.users.services.presence import is_active

logger = logging.getLogger(__name__)

PUSH_COOLDOWN_SECONDS = 60
ACTIVE_RETRY_DELAY_SECONDS = 120
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
def send_push_notification(notification_uuid: str, is_retry: bool = False):
    """Send a Web Push notification to all of the recipient's subscriptions.

    When the recipient is actively using the app, the push is deferred once
    instead of dropped: if the notification is still unread after the delay,
    the user is present but has not seen it (background tab, second screen),
    which is exactly when a push helps. The retry run skips the activity gate.
    """
    private_key = getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "")
    if not private_key:
        logger.warning("Push skipped: WEBPUSH_VAPID_PRIVATE_KEY is not configured")
        return

    vapid_claims = getattr(settings, "WEBPUSH_VAPID_CLAIMS", {})
    if not vapid_claims.get("sub"):
        logger.warning(
            "Push skipped: WEBPUSH_VAPID_MAILTO is not configured (vapid_claims.sub is empty)"
        )
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
        logger.info(
            "Push skipped for user %s: notification already read", notif.recipient_id
        )
        return

    if not is_retry and is_active(notif.recipient_id):
        logger.info(
            "Push deferred for user %s: active in-app, retrying in %ss",
            notif.recipient_id,
            ACTIVE_RETRY_DELAY_SECONDS,
        )
        send_push_notification.apply_async(
            (notification_uuid,),
            {"is_retry": True},
            countdown=ACTIVE_RETRY_DELAY_SECONDS,
        )
        return

    subscriptions = list(PushSubscription.objects.filter(user=notif.recipient))
    if not subscriptions:
        return

    try:
        signer = load_vapid_key(private_key)
    except VapidKeyError as e:
        # Reported once for the whole run: an unusable key fails every
        # subscription identically, and the per-subscription handler below
        # would bury the cause under one traceback per device.
        logger.error("Push skipped: %s", e)
        return

    cooldown_key = None
    if notif.priority != "urgent":
        cooldown_key = _source_cooldown_key(notif)
        # cache.add is an atomic SET NX on Redis; if the key is already there
        # a push for this (user, source) went out within the window. A Redis
        # outage fails the task, which is acceptable: Redis is also the
        # Celery broker, so the task would not be running anyway.
        if cooldown_key and not cache.add(cooldown_key, 1, PUSH_COOLDOWN_SECONDS):
            logger.info("Push skipped for user %s: source cooldown", notif.recipient_id)
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

    delivered = 0
    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=signer,
                # pywebpush fills `aud` and `exp` into the dict it is handed
                # and only ever revises `exp`. Passing the settings dict itself
                # would pin every later push to the first endpoint's origin,
                # which the other push services reject.
                vapid_claims=dict(vapid_claims),
            )
            delivered += 1
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

    if cooldown_key and delivered == 0:
        # Nothing went out: re-arm the window so the next notification for
        # this source is not throttled by an attempt that delivered nothing.
        cache.delete(cooldown_key)


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
