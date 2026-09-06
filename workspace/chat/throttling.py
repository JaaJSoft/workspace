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
bottom of this file applies one of the throttles below to them, so the rate,
the scope and the identity resolution keep one definition each rather than
being re-derived per view.
"""

import math
from functools import wraps

from django.http import HttpResponse
from django.shortcuts import render
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


class MeetingPublicPageThrottle(MeetingPublicIpThrottle):
    """Same IP-scoped throttle as above, under its own, more generous scope.

    The two public HTML routes - the meeting page and the message list it
    loads - are not the sparse anonymous action ``chat.meeting.public.ip`` is
    sized for. The list is re-fetched whenever the guest's SSE stream reports
    a message, so it follows the conversation's cadence: a lively meeting
    produces a request per event, and a reconnect re-loads it outright. That
    is a machine-driven, per-participant pattern, the same shape as the
    heartbeat scope, and several guests behind one NAT share the bucket the
    same way - so it gets its own budget rather than eating the one sized for
    knocking and joining. v1 starting value; retune on telemetry, same as the
    two above.
    """

    scope = "chat.meeting.public.page"


def _throttled_response(throttle, request, template):
    """The 429 a throttled anonymous page answers with.

    ``Retry-After`` is what DRF's own ``throttled()`` emits, computed the same
    way from the throttle's ``wait()`` - a client that respects it stops
    hammering, which is the whole point of answering rather than dropping.
    """
    wait = throttle.wait()
    seconds = math.ceil(wait) if wait is not None else None
    if template is None:
        response = HttpResponse(
            "Too many requests", status=429, content_type="text/plain"
        )
    else:
        response = render(request, template, {"retry_after": seconds}, status=429)
    if seconds is not None:
        response["Retry-After"] = str(seconds)
    return response


def meeting_public_ip_limited(
    view_func=None, *, throttle_class=MeetingPublicPageThrottle, template=None
):
    """Run a meeting throttle in front of a plain Django view.

    Usable bare (``@meeting_public_ip_limited``) or with arguments. *template*
    names a standalone page to answer a throttled browser with; left None the
    view answers text/plain, which is what an ``$ajax`` target wants.

    The request is wrapped in a DRF ``Request`` so the throttle sees exactly
    the object it sees on the API path.

    A signed-in caller skips the bucket entirely. Every scope here is keyed by
    IP and sized for the anonymous surface, where the only identity available
    is the address - so a meeting's guests, their message lists refetching on
    every event, all spend one budget from the office NAT they share with the
    host. Charging the host's own page load to that same budget would let the
    guests lock them out of the room they are hosting. A session is an
    identity the address is not: it is attributable, it is revocable, and the
    views behind this decorator re-check their own access (``meet_view``
    resolves membership before redirecting, ``meet_messages_view`` still
    demands a meeting token). The fence in ``test_guest_containment`` reads
    decorators, not branches, so the decorator stays on every public page.
    """

    def _decorate(func):
        @wraps(func)
        def _limited(request, *args, **kwargs):
            if request.user.is_authenticated:
                return func(request, *args, **kwargs)
            throttle = throttle_class()
            if not throttle.allow_request(Request(request), None):
                return _throttled_response(throttle, request, template)
            return func(request, *args, **kwargs)

        return _limited

    return _decorate if view_func is None else _decorate(view_func)
