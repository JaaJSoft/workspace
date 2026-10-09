from django.urls import path, re_path

from .views import caldav, calendars, events, external, feeds, invitees, polls

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
    path(
        "api/v1/calendars/<uuid:calendar_id>/feed",
        feeds.CalendarFeedView.as_view(),
        name="calendar-feed-settings",
    ),
    path("calendar/feeds/<str:token>.ics", feeds.calendar_feed, name="calendar-feed"),
    path("api/v1/events", events.EventListView.as_view(), name="calendar-events"),
    path(
        "api/v1/events/invitees",
        invitees.InviteeResolveView.as_view(),
        name="calendar-invitees-resolve",
    ),
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
    # CalDAV. Collections answer with or without their trailing slash: the
    # hrefs we serve carry it, but clients are not consistent about keeping it.
    re_path(r"^\.well-known/caldav/?$", caldav.well_known, name="caldav-well-known"),
    re_path(r"^caldav/?$", caldav.CalDavRootView.as_view(), name="caldav-root"),
    re_path(
        r"^caldav/principals/?$",
        caldav.CalDavRootView.as_view(),
        name="caldav-principals",
    ),
    re_path(
        r"^caldav/principals/(?P<username>[^/]+)/?$",
        caldav.PrincipalView.as_view(),
        name="caldav-principal",
    ),
    re_path(
        r"^caldav/calendars/(?P<username>[^/]+)/?$",
        caldav.CalendarHomeView.as_view(),
        name="caldav-home",
    ),
    re_path(
        r"^caldav/calendars/(?P<username>[^/]+)/(?P<calendar_id>[0-9a-fA-F-]{32,36})/?$",
        caldav.CalendarCollectionView.as_view(),
        name="caldav-calendar",
    ),
    re_path(
        r"^caldav/calendars/(?P<username>[^/]+)/(?P<calendar_id>[0-9a-fA-F-]{32,36})/(?P<name>[^/]+)$",
        caldav.CalendarObjectView.as_view(),
        name="caldav-object",
    ),
]
