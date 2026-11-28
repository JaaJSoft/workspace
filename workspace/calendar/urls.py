from django.urls import path, re_path

from workspace.common.uuids import UUID_PATTERN

from .views import caldav, calendars, events, feeds, invitees, polls

urlpatterns = [
    path(
        "api/v1/calendars",
        calendars.CalendarListView.as_view(),
        name="calendar-list",
    ),
    path(
        "api/v1/calendars/<uuid:uuid>",
        calendars.CalendarDetailView.as_view(),
        name="calendar-detail",
    ),
    path(
        "api/v1/calendars/<uuid:uuid>/sync",
        calendars.CalendarSyncView.as_view(),
        name="calendar-sync",
    ),
    path(
        "api/v1/calendars/<uuid:uuid>/feed",
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
        "api/v1/events/<uuid:uuid>",
        events.EventDetailView.as_view(),
        name="calendar-event-detail",
    ),
    path(
        "api/v1/events/<uuid:uuid>/response",
        events.EventResponseView.as_view(),
        name="calendar-event-response",
    ),
    path("api/v1/calendar/polls", polls.PollListView.as_view(), name="poll-list"),
    path(
        "api/v1/calendar/polls/shared/<str:token>",
        polls.SharedPollView.as_view(),
        name="poll-shared",
    ),
    path(
        "api/v1/calendar/polls/shared/<str:token>/vote",
        polls.SharedPollVoteView.as_view(),
        name="poll-shared-vote",
    ),
    path(
        "api/v1/calendar/polls/<uuid:uuid>",
        polls.PollDetailView.as_view(),
        name="poll-detail",
    ),
    path(
        "api/v1/calendar/polls/<uuid:uuid>/vote",
        polls.PollVoteView.as_view(),
        name="poll-vote",
    ),
    path(
        "api/v1/calendar/polls/<uuid:uuid>/finalize",
        polls.PollFinalizeView.as_view(),
        name="poll-finalize",
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
        rf"^caldav/calendars/(?P<username>[^/]+)/(?P<calendar_id>{UUID_PATTERN})/?$",
        caldav.CalendarCollectionView.as_view(),
        name="caldav-calendar",
    ),
    re_path(
        rf"^caldav/calendars/(?P<username>[^/]+)/(?P<calendar_id>{UUID_PATTERN})/(?P<name>[^/]+)$",
        caldav.CalendarObjectView.as_view(),
        name="caldav-object",
    ),
]
