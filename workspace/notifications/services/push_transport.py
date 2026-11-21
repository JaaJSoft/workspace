"""The HTTP side of a Web Push delivery.

A subscription's endpoint is whatever URL a browser - or anyone holding a
session - registered, and the push task POSTs to it from the worker. Both the
registration and every delivery therefore go through the URL guard.
"""

from urllib.parse import urlparse

import httpx2

from workspace.common.url_guard import (
    UnsafeUrl,
    check_resolved_url,
    guarded_transport,
    read_capped,
)

# A push service answers with a status and at most a short error message.
MAX_RESPONSE_BYTES = 64 * 1024


def check_push_endpoint(endpoint: str) -> None:
    """Refuse an endpoint no push service would hand out: anything but
    ``https://``, or a host that does not resolve to public addresses only.
    Raises ``UnsafeUrl``."""
    if urlparse(endpoint).scheme != "https":
        raise UnsafeUrl("A push endpoint must be an https:// URL.")
    check_resolved_url(endpoint)


class GuardedPushSession:
    """Stands in for the ``requests.Session`` pywebpush posts through.

    pywebpush speaks ``requests``, which the guard cannot wrap; this sends the
    same POST through a guarded httpx client instead. Redirects are not
    followed: the encrypted payload and the VAPID signature are for the
    endpoint's origin, and a push service has no reason to redirect.

    *deadline*, a ``time.monotonic()`` instant, ends the POST wherever it is.
    """

    def __init__(self, deadline: float | None = None):
        self._client = httpx2.Client(
            transport=guarded_transport(deadline=deadline), follow_redirects=False
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()

    def post(self, url, *, data=None, headers=None, timeout=None):
        with self._client.stream(
            "POST", url, content=data, headers=dict(headers or {}), timeout=timeout
        ) as response:
            body = read_capped(response, MAX_RESPONSE_BYTES)
            return _PushResponse(response, body)


class _PushResponse:
    """The part of ``requests.Response`` pywebpush and the task read."""

    def __init__(self, response, body):
        self.status_code = response.status_code
        self.reason = response.reason_phrase
        self.headers = response.headers
        self.text = body.decode(response.encoding or "utf-8", errors="replace")
