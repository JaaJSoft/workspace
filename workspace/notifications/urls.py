from django.urls import path

from . import views

urlpatterns = [
    path(
        "api/v1/notifications",
        views.NotificationListView.as_view(),
        name="notifications-list",
    ),
    path(
        "api/v1/notifications/mark-all-read",
        views.NotificationMarkAllReadView.as_view(),
        name="notifications-mark-all-read",
    ),
    path(
        "api/v1/notifications/push/key",
        views.PushVapidKeyView.as_view(),
        name="push-vapid-key",
    ),
    path(
        "api/v1/notifications/push/subscriptions",
        views.PushSubscriptionListView.as_view(),
        name="push-subscription-list",
    ),
    path(
        "api/v1/notifications/push/subscriptions/<uuid:uuid>",
        views.PushSubscriptionDetailView.as_view(),
        name="push-subscription-detail",
    ),
    path(
        "api/v1/notifications/<uuid:uuid>",
        views.NotificationDetailView.as_view(),
        name="notifications-detail",
    ),
]
