import base64
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait

import httpx2
import orjson
from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from workspace.common.logging import scrub
from workspace.common.task_priority import BACKGROUND_PRIORITY, INTERACTIVE_PRIORITY
from workspace.common.url_guard import UnsafeUrl
from workspace.notifications.models import SOURCE_FIELD_NAMES, Notification
from workspace.notifications.services.push_transport import GuardedPushSession
from workspace.notifications.services.vapid import VapidKeyError, load_vapid_key
from workspace.users.services.presence import is_active

logger = logging.getLogger(__name__)

PUSH_COOLDOWN_SECONDS = 60
PUSH_TIMEOUT_SECONDS = 10  # per connect and per read, not for the whole send
# Every send of a run, queued ones included, ends by then: below the soft time
# limit, and no thread outlives the task on a server that answers slowly.
PUSH_DEADLINE_SECONDS = 20
MAX_CONCURRENT_PUSHES = 8
ACTIVE_RETRY_DELAY_SECONDS = 60
RETENTION_DAYS = 90
_SOURCE_ID_ATTRS = tuple(f"{name}_id" for name in SOURCE_FIELD_NAMES)

# How long the push service keeps a message for a device it cannot reach
# (asleep in doze, offline, switched off). pywebpush defaults to a TTL of 0,
# which means "deliver now or drop", so a phone that is not awake at the
# instant of sending never gets the notification. A day covers a night and a
# day without signal; anything older is still in the in-app list.
PUSH_TTL_SECONDS = 24 * 60 * 60

# RFC 8030 Urgency: lets the push service and the OS decide whether the
# message is worth waking a device that saves battery. A missing header reads
# as "normal", so a mention would wait in line with everything else.
PUSH_URGENCY = {
    Notification.Priority.LOW: "low",
    Notification.Priority.NORMAL: "normal",
    Notification.Priority.HIGH: "high",
    Notification.Priority.URGENT: "high",
}


def _source_ref(notif):
    """(attr, value) for the notification's source FK, None when sourceless."""
    for attr in _SOURCE_ID_ATTRS:
        value = getattr(notif, attr)
        if value:
            return attr, value
    return None


def _source_cooldown_key(notif):
    ref = _source_ref(notif)
    if ref is None:
        return None
    attr, value = ref
    return f"push:cd:{notif.recipient_id}:{attr}:{value}"


def _notification_tag(notif):
    """Tag the OS uses to decide what a notification replaces.

    Must be per source: a single tag per module makes every new notification
    overwrite the previous one on screen, so the user never sees the first.
    Sourceless notifications fall back to their own uuid so two announcements
    never collapse into one.
    """
    ref = _source_ref(notif)
    if ref is None:
        return f"notif:{notif.uuid}"
    attr, value = ref
    return f"{attr}:{value}"


def _push_topic(notif):
    """RFC 8030 Topic: a message still queued for a sleeping device is
    replaced by the next one carrying the same topic.

    Derived from the display tag, so the push service collapses exactly what
    the OS would replace on screen anyway. A topic is at most 32 characters of
    the URL-safe base64 alphabet, which the tag (a colon and a UUID) is not,
    so it is hashed: 24 bytes encode to 32 characters with no padding.
    """
    digest = hashlib.sha256(_notification_tag(notif).encode()).digest()[:24]
    return base64.urlsafe_b64encode(digest).decode()


@shared_task(
    name="notifications.send_push",
    priority=INTERACTIVE_PRIORITY,
    ignore_result=True,
    soft_time_limit=30,
)
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

    # pywebpush pulls in aiohttp; the web process imports this module to
    # queue the task and never sends a push itself.
    from pywebpush import WebPushException, webpush

    from workspace.notifications.models import PushSubscription

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
            "tag": _notification_tag(notif),
        }
    )
    headers = {
        "Urgency": PUSH_URGENCY[notif.priority],
        "Topic": _push_topic(notif),
    }

    deadline = time.monotonic() + PUSH_DEADLINE_SECONDS

    def send(subscription_info):
        with GuardedPushSession(deadline=deadline) as session:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=signer,
                # pywebpush fills `aud` and `exp` into the dict it is handed
                # and only ever revises `exp`. Passing the settings dict itself
                # would pin every later push to the first endpoint's origin,
                # which the other push services reject.
                vapid_claims=dict(vapid_claims),
                ttl=PUSH_TTL_SECONDS,
                headers=headers,
                requests_session=session,
                timeout=PUSH_TIMEOUT_SECONDS,
            )

    delivered = 0
    try:
        # The pool threads only talk HTTP: the database, the cache and the
        # logs stay on this thread.
        pool = ThreadPoolExecutor(
            max_workers=min(len(subscriptions), MAX_CONCURRENT_PUSHES),
            thread_name_prefix="webpush",
        )
        try:
            futures = {
                sub: pool.submit(
                    send,
                    {
                        "endpoint": sub.endpoint,
                        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                    },
                )
                for sub in subscriptions
            }
            # A second of grace lets a send cut off at the deadline report it.
            done, _ = wait(futures.values(), timeout=PUSH_DEADLINE_SECONDS + 1)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        for sub, future in futures.items():
            if future not in done:
                logger.warning(
                    "Push to %s did not complete within %ss",
                    scrub(sub.endpoint[:60]),
                    PUSH_DEADLINE_SECONDS,
                )
                continue
            try:
                future.result()
            except UnsafeUrl as e:
                # Kept: "could not be resolved" is a refusal too, and a DNS
                # outage must not drop every subscription.
                logger.warning(
                    "Push refused for %s: %s", scrub(sub.endpoint[:60]), e.user_message
                )
            except httpx2.TimeoutException:
                logger.warning("Push to %s timed out", scrub(sub.endpoint[:60]))
            except WebPushException as e:
                status_code = getattr(e.response, "status_code", None)
                if status_code in (404, 410):
                    sub.delete()
                    logger.info(
                        "Deleted expired push subscription %s",
                        scrub(sub.endpoint[:60]),
                    )
                else:
                    # The message carries the push service's response body.
                    logger.warning(
                        "Push failed for %s: %s",
                        scrub(sub.endpoint[:60]),
                        scrub(str(e)[:300]),
                    )
            except Exception:
                logger.exception(
                    "Unexpected error sending push to %s", scrub(sub.endpoint[:60])
                )
            else:
                delivered += 1
    finally:
        if cooldown_key and delivered == 0:
            # Nothing went out: re-arm the window so the next notification for
            # this source is not throttled by an attempt that delivered
            # nothing. In a finally so a time limit hit mid-run releases it too.
            cache.delete(cooldown_key)


@shared_task(
    name="notifications.prune_read", priority=BACKGROUND_PRIORITY, ignore_result=True
)
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
