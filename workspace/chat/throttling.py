"""Rate limiting for the meeting module's unauthenticated surface.

Every view in ``workspace.chat.views.meetings`` and ``workspace.chat.views.
meeting_guest`` that a guest reaches runs with ``authentication_classes =
[]``, so ``request.user`` is always ``AnonymousUser``. DRF's own
``AnonRateThrottle`` would technically apply here, but its ``get_ident``
trusts the whole ``X-Forwarded-For`` header when ``NUM_PROXIES`` is unset
(the default), letting a caller mint a fresh identity per request and defeat
the limit. ``workspace.common.request_ip.client_ip`` does not have that gap,
so all three throttles below use it instead.

The public *pages* under /meet are plain Django views, which no DRF
machinery ever runs a throttle for; ``meeting_public_ip_limited`` at the
bottom of this file applies the first throttle below to them, so the rate,
the scope and the identity resolution have one definition for the whole
anonymous surface rather than two that can drift.
"""

from functools import wraps

from django.http import HttpResponse
from rest_framework.request import Request
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


class MeetingGuestSignalThrottle(MeetingPublicIpThrottle):
    """Same IP-scoped throttle as above, under its own, more generous scope.

    Signalling trickles one POST per ICE candidate per peer connection, on
    top of the offer/answer exchange itself - a browser's own gathering
    typically emits somewhere around 5-15 candidates per peer. A guest
    joining a call with the other CHAT_CALL_MAX_PARTICIPANTS - 1 participants
    already present (5, at the default cap of 6) can burst through most of
    that exchange with every one of them in the first few seconds after
    joining - call it 5 peers x ~15 candidates, plus the offer/answer
    messages themselves, comfortably past the shared 30/min budget above on
    its own. Several guests behind one NAT/IP compound it further, same as
    the heartbeat scope. v1 starting value; retune on telemetry, same as it.
    """

    scope = "chat.meeting.guest.signal.ip"


def meeting_public_ip_limited(view_func):
    """Run ``MeetingPublicIpThrottle`` in front of a plain Django view.

    Deliberately the same scope as the JSON endpoints rather than one of its
    own: a guest loads the page and its message list from the same address
    that then knocks, joins and posts, so "30 anonymous requests a minute
    from here" is one budget, not several that each look small.

    The request is wrapped in a DRF ``Request`` so the throttle sees exactly
    the object it sees on the API path. 429 answers as plain text: the caller
    is either a script or a browser that has already been served the page.
    """

    @wraps(view_func)
    def _limited(request, *args, **kwargs):
        if not MeetingPublicIpThrottle().allow_request(Request(request), None):
            return HttpResponse(
                "Too many requests", status=429, content_type="text/plain"
            )
        return view_func(request, *args, **kwargs)

    return _limited
