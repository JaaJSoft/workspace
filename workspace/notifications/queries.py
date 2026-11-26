from .models import Notification, PushSubscription


def user_notifications(user):
    """The notifications addressed to *user*, newest first."""
    return (
        Notification.objects.filter(recipient=user)
        .select_related("actor")
        .order_by("-created_at", "-uuid")
    )


def user_push_subscriptions(user):
    """The browsers *user* registered for push, oldest first."""
    return PushSubscription.objects.filter(user=user).order_by("created_at", "uuid")
