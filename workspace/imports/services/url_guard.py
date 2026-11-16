"""Guard for user-supplied remote URLs.

The worker fetches whatever URL a connection carries, so an unchecked URL is
a server-side request forgery: loopback, link-local (cloud metadata) and, by
default, private networks are refused. Self-hosters whose previous cloud lives
on the LAN open it with IMPORTS_ALLOW_PRIVATE_NETWORKS or list the host in
IMPORTS_ALLOWED_HOSTS.

A hostname is vetted where the connection is opened, by ``guarded_transport()``:
it resolves the name once, refuses it when any answer is off limits, and dials
the addresses it checked. Resolving it a second time to connect is what would
let a name answer the check with a public address and the connection with a
private one (DNS rebinding). TLS still runs against the hostname, so SNI and
the certificate check are those of the URL.
"""

import socket
from ipaddress import ip_address
from urllib.parse import urlparse

import httpcore2
import httpx2
from django.conf import settings
from django.utils.translation import gettext as _

from ..errors import ImportsError


class UnsafeUrl(ImportsError):
    pass


def check_remote_url(url: str) -> None:
    """Refuse a URL no guarded request could fetch, without resolving it: a
    scheme other than http(s), no host, or an IP address that is off limits.
    A hostname's addresses are checked when ``guarded_transport()`` connects."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrl(_("Only http:// and https:// URLs are supported."))
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeUrl(_("The URL has no host."))
    if _is_allowed_host(host):
        return
    try:
        address = ip_address(host)
    except ValueError:
        return
    _check_address(address, host)


def guarded_transport() -> httpx2.HTTPTransport:
    """An HTTP transport that only connects to addresses the guard accepts.

    A client given an explicit transport ignores the proxy environment
    variables: a proxy would resolve the name itself, out of the guard's reach.
    """
    transport = httpx2.HTTPTransport()
    # httpx2 has no parameter for it; the pool reads this attribute each time
    # it opens a connection. test_url_guard fails if an upgrade moves it.
    transport._pool._network_backend = _GuardedBackend()
    return transport


class _GuardedBackend(httpcore2.SyncBackend):
    def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        if _is_allowed_host(host):
            return super().connect_tcp(
                host, port, timeout, local_address, socket_options
            )
        error = None
        # Every answer is vetted before the first is dialed, then they are
        # tried in the resolver's order, as socket.create_connection would.
        for address in _vetted_addresses(host):
            try:
                return super().connect_tcp(
                    str(address), port, timeout, local_address, socket_options
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as exc:
                error = exc
        raise error


def _is_allowed_host(host):
    return host.lower() in {h.lower() for h in settings.IMPORTS_ALLOWED_HOSTS}


def _vetted_addresses(host):
    addresses = _resolve(host)
    for address in addresses:
        _check_address(address, host)
    return addresses


def _resolve(host):
    try:
        return (ip_address(host),)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise _unresolved(host) from exc
    addresses = tuple(dict.fromkeys(ip_address(info[4][0]) for info in infos))
    if not addresses:
        raise _unresolved(host)
    return addresses


def _unresolved(host):
    return UnsafeUrl(_("'%(host)s' could not be resolved.") % {"host": host})


def _check_address(address, host):
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        raise UnsafeUrl(
            _("'%(host)s' points to an address this server will not contact.")
            % {"host": host}
        )
    # Not-global rather than is_private: it also covers carrier-grade NAT
    # (100.64/10) and other ranges that are routable inside a network but
    # never on the Internet.
    if not address.is_global and not settings.IMPORTS_ALLOW_PRIVATE_NETWORKS:
        raise UnsafeUrl(
            _(
                "'%(host)s' is on a private network. Ask the administrator to "
                "allow private networks for imports if your previous cloud "
                "lives there."
            )
            % {"host": host}
        )
