from django.urls import path

from . import views
from . import views_polls

urlpatterns = [
    path('api/v1/calendar/calendars', views.CalendarListView.as_view(), name='calendar-list'),
    path('api/v1/calendar/calendars/<uuid:calendar_id>', views.CalendarDetailView.as_view(), name='calendar-detail'),
    path('api/v1/calendar/events', views.EventListView.as_view(), name='calendar-events'),
    path('api/v1/calendar/events/<uuid:event_id>', views.EventDetailView.as_view(), name='calendar-event-detail'),
    path('api/v1/calendar/events/<uuid:event_id>/respond', views.EventRespondView.as_view(), name='calendar-event-respond'),

    # Polls
    path('api/v1/calendar/polls', views_polls.PollListView.as_view(), name='poll-list'),
    path('api/v1/calendar/polls/shared/<str:token>', views_polls.SharedPollView.as_view(), name='poll-shared'),
    path('api/v1/calendar/polls/shared/<str:token>/vote', views_polls.SharedPollVoteView.as_view(), name='poll-shared-vote'),
    path('api/v1/calendar/polls/<uuid:poll_id>', views_polls.PollDetailView.as_view(), name='poll-detail'),
    path('api/v1/calendar/polls/<uuid:poll_id>/vote', views_polls.PollVoteView.as_view(), name='poll-vote'),
    path('api/v1/calendar/polls/<uuid:poll_id>/finalize', views_polls.PollFinalizeView.as_view(), name='poll-finalize'),
]
