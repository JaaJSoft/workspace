"""Guard for user-supplied remote URLs.

The worker fetches whatever URL a connection carries, so an unchecked URL is
a server-side request forgery: loopback, link-local (cloud metadata) and, by
default, private networks are refused after resolving the host. Self-hosters
whose previous cloud lives on the LAN open it with IMPORTS_ALLOW_PRIVATE_NETWORKS
or list the host in IMPORTS_ALLOWED_HOSTS.

The check resolves the host itself and the HTTP client resolves it again, so
a name that flips between two answers (DNS rebinding) is not caught by it -
callers re-run it before every batch of requests to keep that window short,
but only pinning the resolved address at the transport level would close it.
"""

import socket
from ipaddress import ip_address
from urllib.parse import urlparse

from django.conf import settings

from ..errors import ImportsError


class UnsafeUrl(ImportsError):
    pass


def check_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrl("Only http:// and https:// URLs are supported.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeUrl("The URL has no host.")
    if host in {h.lower() for h in settings.IMPORTS_ALLOWED_HOSTS}:
        return
    for address in _resolve(host):
        _check_address(address, host)


def _resolve(host):
    try:
        return (ip_address(host),)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrl(f"'{host}' could not be resolved.") from exc
    addresses = {ip_address(info[4][0]) for info in infos}
    if not addresses:
        raise UnsafeUrl(f"'{host}' could not be resolved.")
    return addresses


def _check_address(address, host):
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        raise UnsafeUrl(f"'{host}' points to an address this server will not contact.")
    # Not-global rather than is_private: it also covers carrier-grade NAT
    # (100.64/10) and other ranges that are routable inside a network but
    # never on the Internet.
    if not address.is_global and not settings.IMPORTS_ALLOW_PRIVATE_NETWORKS:
        raise UnsafeUrl(
            f"'{host}' is on a private network. Ask the administrator to allow "
            "private networks for imports if your previous cloud lives there."
        )
