from django.urls import path

from .views import calendars, events, external, polls

urlpatterns = [
    path(
        "api/v1/calendars",
        calendars.CalendarListView.as_view(),
        name="calendar-list",
    ),
    path(
        "api/v1/calendars/<uuid:calendar_id>",
        calendars.CalendarDetailView.as_view(),
        name="calendar-detail",
    ),
    path("api/v1/events", events.EventListView.as_view(), name="calendar-events"),
    path(
        "api/v1/events/<uuid:event_id>",
        events.EventDetailView.as_view(),
        name="calendar-event-detail",
    ),
    path(
        "api/v1/events/<uuid:event_id>/respond",
        events.EventRespondView.as_view(),
        name="calendar-event-respond",
    ),
    # Polls
    path("api/v1/polls", polls.PollListView.as_view(), name="poll-list"),
    path(
        "api/v1/polls/shared/<str:token>",
        polls.SharedPollView.as_view(),
        name="poll-shared",
    ),
    path(
        "api/v1/polls/shared/<str:token>/vote",
        polls.SharedPollVoteView.as_view(),
        name="poll-shared-vote",
    ),
    path(
        "api/v1/polls/<uuid:poll_id>",
        polls.PollDetailView.as_view(),
        name="poll-detail",
    ),
    path(
        "api/v1/polls/<uuid:poll_id>/vote",
        polls.PollVoteView.as_view(),
        name="poll-vote",
    ),
    path(
        "api/v1/polls/<uuid:poll_id>/invite",
        polls.PollInviteView.as_view(),
        name="poll-invite",
    ),
    path(
        "api/v1/polls/<uuid:poll_id>/finalize",
        polls.PollFinalizeView.as_view(),
        name="poll-finalize",
    ),
    # External calendars
    path(
        "api/v1/external-calendars",
        external.ExternalCalendarListView.as_view(),
        name="external-calendar-list",
    ),
    path(
        "api/v1/external-calendars/<uuid:ext_id>",
        external.ExternalCalendarDetailView.as_view(),
        name="external-calendar-detail",
    ),
    path(
        "api/v1/external-calendars/<uuid:ext_id>/sync",
        external.ExternalCalendarSyncView.as_view(),
        name="external-calendar-sync",
    ),
]
