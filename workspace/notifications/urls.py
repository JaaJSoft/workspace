from django.urls import path
from . import views

urlpatterns = [
    path('api/v1/notifications', views.NotificationListView.as_view(), name='notifications-list'),
    path('api/v1/notifications/read-all', views.NotificationReadAllView.as_view(), name='notifications-read-all'),
    path('api/v1/notifications/<uuid:notification_id>', views.NotificationDetailView.as_view(), name='notifications-detail'),
]
