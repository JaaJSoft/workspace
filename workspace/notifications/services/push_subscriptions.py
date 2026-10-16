import hmac

from django.db import transaction

from ..models import PushSubscription


def _same_secret(stored, given):
    # compare_digest refuses non-ASCII str, which a request can carry.
    return hmac.compare_digest(stored.encode(), given.encode())


def _holds_keys(subscription, p256dh, auth):
    return _same_secret(subscription.p256dh, p256dh) and _same_secret(
        subscription.auth, auth
    )


def save_push_subscription(user, endpoint, p256dh, auth):
    """Register *endpoint* as a push target of *user*; False when refused.

    The endpoint is the subscription's identity, so a browser re-subscribing
    under another login takes the row over - but only by presenting the keys
    stored with it, which only the browser holding that subscription knows.
    Without them, the row stays with its owner.
    """
    with transaction.atomic():
        subscription, created = (
            PushSubscription.objects.select_for_update().get_or_create(
                endpoint=endpoint,
                defaults={"user": user, "p256dh": p256dh, "auth": auth},
            )
        )
        if created:
            return True
        if subscription.user_id != user.pk and not _holds_keys(
            subscription, p256dh, auth
        ):
            return False
        subscription.user = user
        subscription.p256dh = p256dh
        subscription.auth = auth
        subscription.save(update_fields=["user", "p256dh", "auth"])
    return True
