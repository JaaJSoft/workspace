import base64
import hashlib
import logging
import uuid

import orjson
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
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
# A send runs as a task of its own so one device's push service cannot hold up
# another's. The soft limit ends a send the per-read timeout never trips (a
# server answering a byte at a time); the hard limit kills one that ignores it.
PUSH_SEND_SOFT_TIME_LIMIT = 15
PUSH_SEND_TIME_LIMIT = 20
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
        load_vapid_key(private_key)
    except VapidKeyError as e:
        # Reported once for the whole run: an unusable key fails every
        # subscription identically, and each send would bury the cause under
        # one traceback per device.
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
    ).decode()
    headers = {
        "Urgency": PUSH_URGENCY[notif.priority],
        "Topic": _push_topic(notif),
    }

    run_id = None
    if cooldown_key:
        run_id = uuid.uuid4().hex
        cache.set(
            _run_key(run_id, "pending"), len(subscriptions), PUSH_COOLDOWN_SECONDS
        )
    for sub in subscriptions:
        deliver_push.apply_async(
            (str(sub.pk), payload, headers),
            {"cooldown_key": cooldown_key, "run_id": run_id},
        )


@shared_task(
    name="notifications.deliver_push",
    priority=INTERACTIVE_PRIORITY,
    ignore_result=True,
    soft_time_limit=PUSH_SEND_SOFT_TIME_LIMIT,
    time_limit=PUSH_SEND_TIME_LIMIT,
)
def deliver_push(
    subscription_uuid: str,
    payload: str,
    headers: dict,
    cooldown_key: str | None = None,
    run_id: str | None = None,
):
    """Send one push to one of the recipient's subscriptions."""
    delivered = False
    try:
        delivered = _send_to_subscription(subscription_uuid, payload, headers)
    finally:
        if run_id:
            _settle_cooldown(run_id, cooldown_key, delivered)


def _send_to_subscription(subscription_uuid, payload, headers):
    """Whether the push service accepted the message."""
    # pywebpush pulls in aiohttp; the web process imports this module to
    # queue the task and never sends a push itself.
    from pywebpush import WebPushException, webpush

    from workspace.notifications.models import PushSubscription

    sub = PushSubscription.objects.filter(pk=subscription_uuid).first()
    if sub is None:
        return False
    endpoint = scrub(sub.endpoint[:60])
    try:
        with GuardedPushSession() as session:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=load_vapid_key(settings.WEBPUSH_VAPID_PRIVATE_KEY),
                # pywebpush fills `aud` and `exp` into the dict it is handed
                # and only ever revises `exp`: never hand it the settings dict.
                vapid_claims=dict(settings.WEBPUSH_VAPID_CLAIMS),
                ttl=PUSH_TTL_SECONDS,
                headers=headers,
                requests_session=session,
                timeout=PUSH_TIMEOUT_SECONDS,
            )
    except SoftTimeLimitExceeded:
        logger.warning("Push to %s timed out", endpoint)
    except UnsafeUrl as e:
        # Kept: "could not be resolved" is a refusal too, and a DNS outage
        # must not drop every subscription.
        logger.warning("Push refused for %s: %s", endpoint, e.user_message)
    except WebPushException as e:
        if getattr(e.response, "status_code", None) in (404, 410):
            sub.delete()
            logger.info("Deleted expired push subscription %s", endpoint)
        else:
            # The message carries the push service's response body.
            logger.warning("Push failed for %s: %s", endpoint, scrub(str(e)[:300]))
    except Exception:
        logger.exception("Unexpected error sending push to %s", endpoint)
    else:
        return True
    return False


def _run_key(run_id, name):
    return f"push:run:{run_id}:{name}"


def _settle_cooldown(run_id, cooldown_key, delivered):
    """Release the source cooldown once every send of the run has finished
    and none of them delivered, so a run that reached no device does not
    throttle the next notification. A partial delivery keeps it.

    A send killed by its hard time limit never reports: the cooldown then
    lasts its full window, which is all it costs.
    """
    if delivered:
        # Written before the count goes down, so the send that brings it to
        # zero sees it.
        cache.set(_run_key(run_id, "delivered"), 1, PUSH_COOLDOWN_SECONDS)
    try:
        left = cache.decr(_run_key(run_id, "pending"))
    except ValueError:
        return  # the count expired, and the cooldown along with it
    if left > 0:
        return
    if not cache.get(_run_key(run_id, "delivered")):
        cache.delete(cooldown_key)
    cache.delete_many([_run_key(run_id, "pending"), _run_key(run_id, "delivered")])


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
