"""Guard for user-supplied remote URLs.

The worker fetches whatever URL a connection carries, so an unchecked URL is
a server-side request forgery: loopback, link-local (cloud metadata) and, by
default, private networks are refused after resolving the host. Self-hosters
whose previous cloud lives on the LAN open it with IMPORTS_ALLOW_PRIVATE_NETWORKS
or list the host in IMPORTS_ALLOWED_HOSTS.
"""

import socket
from ipaddress import ip_address
from urllib.parse import urlparse

from django.conf import settings


class UnsafeUrl(ValueError):
    """The message is safe to show to the user."""


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
    if address.is_private and not settings.IMPORTS_ALLOW_PRIVATE_NETWORKS:
        raise UnsafeUrl(
            f"'{host}' is on a private network. Ask the administrator to allow "
            "private networks for imports if your previous cloud lives there."
        )
