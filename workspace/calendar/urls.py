from django.urls import path

from . import views

urlpatterns = [
    path('api/v1/calendar/calendars', views.CalendarListView.as_view(), name='calendar-list'),
    path('api/v1/calendar/calendars/<uuid:calendar_id>', views.CalendarDetailView.as_view(), name='calendar-detail'),
    path('api/v1/calendar/events', views.EventListView.as_view(), name='calendar-events'),
    path('api/v1/calendar/events/<uuid:event_id>', views.EventDetailView.as_view(), name='calendar-event-detail'),
    path('api/v1/calendar/events/<uuid:event_id>/respond', views.EventRespondView.as_view(), name='calendar-event-respond'),
]
