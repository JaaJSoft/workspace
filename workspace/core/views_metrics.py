"""Access-controlled Prometheus metrics endpoint.

``django_prometheus`` ships ``/metrics`` as a fully unauthenticated view. The
payload exposes internal topology — model and view names, per-endpoint request
volumes and latencies, DB/cache backends, migration state — so this module
wraps the exporter behind :func:`_is_allowed`.
"""

import ipaddress
import logging
from functools import lru_cache
from hmac import compare_digest

from django.conf import settings
from django.http import HttpResponse
from django_prometheus import exports

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _parse_networks(entries):
    networks = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning(
                "Ignoring invalid METRICS_ALLOWED_IPS entry %s", scrub(entry)
            )
    return tuple(networks)


def _ip_allowed(remote_addr):
    networks = _parse_networks(tuple(getattr(settings, "METRICS_ALLOWED_IPS", ())))
    if not networks or not remote_addr:
        return False
    try:
        client = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(client in network for network in networks)


def _token_matches(request):
    expected = (getattr(settings, "METRICS_TOKEN", "") or "").strip()
    if not expected:
        return False
    scheme, _, provided = request.META.get("HTTP_AUTHORIZATION", "").partition(" ")
    if scheme.lower() != "bearer":
        return False
    try:
        return compare_digest(provided.strip(), expected)
    except TypeError:  # non-ASCII in either side
        return False


def _is_superuser(request):
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_superuser)


def _is_allowed(request):
    """Grant access when any of the three checks passes.

    The IP check reads ``REMOTE_ADDR`` only — ``X-Forwarded-For`` is attacker
    controlled and trusting it would hand out access to anyone. Behind a
    reverse proxy every request therefore carries the proxy's (usually
    private) address, so the default private-range allowlist grants access to
    the whole internet: such deployments must set ``METRICS_ALLOWED_IPS`` to
    an empty value and scrape with ``METRICS_TOKEN``.
    """
    return (
        _token_matches(request)
        or _ip_allowed(request.META.get("REMOTE_ADDR"))
        or _is_superuser(request)
    )


def metrics_view(request):
    if not _is_allowed(request):
        logger.warning(
            "Rejected /metrics request from %s",
            scrub(request.META.get("REMOTE_ADDR") or "unknown"),
        )
        return HttpResponse("Forbidden", status=403, content_type="text/plain")
    return exports.ExportToDjangoView(request)
