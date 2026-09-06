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

Left unset behind any reverse proxy, ``REMOTE_ADDR`` is the proxy's own
address for every request, so every caller collapses onto one shared bucket
and a per-IP limit built on this helper stops being per-caller at all.
Setting ``NUM_PROXIES`` correctly is therefore a required deployment step
for any endpoint that relies on this helper against real, uncontrolled
traffic - not an optional hardening step.
"""

import ipaddress

from rest_framework.settings import api_settings


def client_ip(request) -> str:
    """The caller's IP address as a validated string, or "" if unusable.

    Validated with ``ipaddress.ip_address`` before being returned, so a
    malformed or spoofed value cannot reach a cache key or a log line
    unbounded - except for an IPv6 zone id (the ``%eth0`` suffix on a
    link-local address), which ``ip_address`` accepts at any length. That
    suffix is stripped before validation so it cannot ride along; it is
    only reachable when ``NUM_PROXIES`` is set higher than the number of
    real hops in front of this app, a deployment misconfiguration rather
    than the default-path bypass this module exists to close.
    """
    num_proxies = api_settings.NUM_PROXIES
    candidate = request.META.get("REMOTE_ADDR", "")
    if num_proxies:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            addrs = xff.split(",")
            candidate = addrs[-min(num_proxies, len(addrs))].strip()

    candidate = candidate.partition("%")[0]

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return ""
    return candidate
