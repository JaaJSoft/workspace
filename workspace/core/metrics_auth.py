"""HTTP Basic auth guard for the Prometheus metrics endpoint.

Django ships decorators for session-backed access (``login_required``,
``user_passes_test``) but none for HTTP Basic, which is what a machine scraper
needs: Prometheus sends these credentials from the ``basic_auth`` block of its
scrape config, and a browser turns the 401 into a login prompt.

Credentials travel base64-encoded, not encrypted, so /metrics must only be
reached over TLS or from inside a trusted network.
"""

import base64
import logging
from functools import wraps
from hmac import compare_digest

from django.conf import settings
from django.http import HttpResponse

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


def _submitted(request):
    """Return the ``(user, password)`` carried by the Authorization header."""
    scheme, _, payload = request.META.get("HTTP_AUTHORIZATION", "").partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        # binascii.Error and UnicodeDecodeError are both ValueError subclasses.
        decoded = base64.b64decode(payload.strip(), validate=True).decode("utf-8")
    except ValueError:
        return None
    user, separator, password = decoded.partition(":")
    return (user, password) if separator else None


def _matches(submitted, expected):
    if submitted is None:
        return False
    # Compared as bytes because compare_digest rejects non-ASCII str, and both
    # halves are always compared so a wrong user costs the same as a wrong password.
    user_ok = compare_digest(submitted[0].encode(), expected[0].encode())
    password_ok = compare_digest(submitted[1].encode(), expected[1].encode())
    return user_ok and password_ok


def metrics_basic_auth(view):
    """Serve ``view`` only to callers presenting METRICS_USER/METRICS_PASSWORD."""

    @wraps(view)
    def guarded(request, *args, **kwargs):
        expected = (settings.METRICS_USER, settings.METRICS_PASSWORD)
        if not all(expected):
            logger.error(
                "/metrics refused a caller: METRICS_USER and METRICS_PASSWORD are unset"
            )
        elif _matches(_submitted(request), expected):
            return view(request, *args, **kwargs)
        else:
            logger.warning(
                "Rejected /metrics request from %s",
                scrub(request.META.get("REMOTE_ADDR") or "unknown"),
            )
        response = HttpResponse("Unauthorized", status=401, content_type="text/plain")
        response["WWW-Authenticate"] = 'Basic realm="metrics", charset="UTF-8"'
        return response

    return guarded
