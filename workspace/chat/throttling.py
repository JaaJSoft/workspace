"""Rate limiting for the meeting module's unauthenticated surface.

Every view in ``workspace.chat.views.meetings`` and ``workspace.chat.views.
meeting_guest`` that a guest reaches runs with ``authentication_classes =
[]``, so ``request.user`` is always ``AnonymousUser``. DRF's own
``AnonRateThrottle`` would technically apply here, but its ``get_ident``
trusts the whole ``X-Forwarded-For`` header when ``NUM_PROXIES`` is unset
(the default), letting a caller mint a fresh identity per request and defeat
the limit. ``workspace.common.request_ip.client_ip`` does not have that gap,
so both throttles below use it instead.
"""

from rest_framework.throttling import SimpleRateThrottle

from workspace.common.request_ip import client_ip


class MeetingPublicIpThrottle(SimpleRateThrottle):
    """Throttle by client IP, for the meeting module's public endpoints.

    A defence-in-depth layer on top of the knock endpoint's own per-meeting
    counter, and the only limit at all on the summary endpoint, which has no
    other rate limiting of its own. Also covers the guest join/leave/state
    endpoints, which are anonymous and DB-hitting (join takes a
    select_for_update) the same way.
    """

    scope = "chat.meeting.public.ip"

    def get_ident(self, request):
        return client_ip(request)

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class MeetingGuestHeartbeatThrottle(MeetingPublicIpThrottle):
    """Same IP-scoped throttle as above, under its own, more generous scope.

    A guest's browser heartbeats every 5s while in a call (see
    ``_heartbeatTimer`` in call.js) - about 12 requests/min per participant,
    which would eat most of the shared 30/min budget above on its own, before
    counting several guests behind one NAT/IP sharing that same bucket. It
    needs a bucket sized for that pattern instead of the "sparse anonymous
    action" one the other public endpoints use.
    """

    scope = "chat.meeting.guest.heartbeat.ip"
