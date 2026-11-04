"""Rate limiting by client IP, for endpoints anyone can reach."""

from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle


class IpRateThrottle(SimpleRateThrottle):
    """Throttle by client IP, authenticated or not.

    ``AnonRateThrottle`` returns ``None`` for an authenticated request, which
    disables it: on an authenticated endpoint, a per-IP limit built on it
    would limit nothing at all - and would read as correct.
    """

    def get_ident(self, request):
        """The peer address, unless a deployment has said how far to trust
        ``X-Forwarded-For``.

        DRF's own implementation falls back to the whole header when
        ``NUM_PROXIES`` is unset, and the header is written by the caller: a
        different value per request buys a fresh bucket, and the limit is gone.
        Only a declared proxy count says which hop is the real peer, so
        without one the header is ignored rather than believed.
        """
        if api_settings.NUM_PROXIES is None:
            return request.META.get("REMOTE_ADDR")
        return super().get_ident(request)

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }
