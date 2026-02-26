from django.urls import path
from . import views

urlpatterns = [
    path('api/v1/notifications', views.NotificationListView.as_view(), name='notifications-list'),
    path('api/v1/notifications/read-all', views.NotificationReadAllView.as_view(), name='notifications-read-all'),
    path('api/v1/notifications/push/key', views.PushVapidKeyView.as_view(), name='push-vapid-key'),
    path('api/v1/notifications/push/subscribe', views.PushSubscribeView.as_view(), name='push-subscribe'),
    path('api/v1/notifications/<uuid:notification_id>', views.NotificationDetailView.as_view(), name='notifications-detail'),
]
