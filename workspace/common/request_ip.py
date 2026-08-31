"""Resolving the caller's IP address for per-IP rate limiting.

``X-Forwarded-For`` is written by the caller, so trusting it without knowing
how many proxy hops are real lets a single caller mint a fresh identity per
request and defeat any limit built on the header. ``NUM_PROXIES``
(``workspace/settings/api.py``) is the deployment's declaration of how many
hops to trust; left unset (the default), the header is ignored entirely and
``REMOTE_ADDR`` - the immediate peer - is used instead. This mirrors
``rest_framework.throttling.SimpleRateThrottle.get_ident``, except DRF's own
version trusts the *whole* header when ``NUM_PROXIES`` is unset, which is
the gap this module exists to close - see ``workspace/vault/throttling.py``,
the first place that mismatch was caught and fixed.
"""

import ipaddress

from rest_framework.settings import api_settings


def client_ip(request) -> str:
    """The caller's IP address as a validated string, or "" if unusable.

    Never returns raw, unvalidated attacker input: a malformed or spoofed
    value (only reachable through ``X-Forwarded-For``, and only when
    ``NUM_PROXIES`` says to trust it) is rejected outright rather than fed
    into a cache key or a log line, where an unbounded string would let an
    attacker mint unlimited distinct keys.
    """
    num_proxies = api_settings.NUM_PROXIES
    candidate = request.META.get("REMOTE_ADDR", "")
    if num_proxies:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            addrs = xff.split(",")
            candidate = addrs[-min(num_proxies, len(addrs))].strip()

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return ""
    return candidate
