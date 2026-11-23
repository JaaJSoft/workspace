"""Guard for the server-side fetch of a URL a user chose.

The server fetches whatever URL a feed, a link or a subscription carries, so an
unchecked URL is a server-side request forgery: loopback, link-local (cloud
metadata) and, unless the caller's policy allows them, private networks are
refused. A module that has a reason to reach the LAN says so through the
``NetworkPolicy`` it passes; the default reaches the public Internet only.

A hostname is vetted where the connection is opened, by ``guarded_transport()``:
it resolves the name once, refuses it when any answer is off limits, and dials
the addresses it checked. Resolving it a second time to connect is what would
let a name answer the check with a public address and the connection with a
private one (DNS rebinding). Every connection goes through that check, so a
client following redirects vets each hop. TLS still runs against the hostname,
so SNI and the certificate check are those of the URL.
"""

import socket
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

import httpcore2
import httpx2
from django.utils.translation import gettext as _


class UnsafeUrl(ValueError):
    """A URL the guard refused. ``user_message`` is built by the guard, never
    from a remote answer, so it can be shown as it is."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class ResponseTooLarge(ValueError):
    def __init__(self, max_bytes: int):
        super().__init__(f"Response larger than {max_bytes} bytes")
        self.max_bytes = max_bytes


@dataclass(frozen=True)
class NetworkPolicy:
    """Where a guarded fetch may connect.

    ``allowed_hosts`` are trusted as they are, without any address check.
    ``private_network_hint`` is appended to the refusal of a private address,
    to tell the user how their administrator can open it.
    """

    allow_private_networks: bool = False
    allowed_hosts: frozenset[str] = frozenset()
    private_network_hint: str = ""

    def __post_init__(self):
        hosts = frozenset(host.lower() for host in self.allowed_hosts)
        object.__setattr__(self, "allowed_hosts", hosts)


PUBLIC_INTERNET = NetworkPolicy()


def check_remote_url(url: str, policy: NetworkPolicy = PUBLIC_INTERNET) -> None:
    """Refuse a URL no guarded request could fetch, without resolving it: a
    scheme other than http(s), no host, a ``localhost`` name, or an IP address
    that is off limits. A hostname's addresses are checked when
    ``guarded_transport()`` connects."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        raise UnsafeUrl(_("The URL has no host.")) from None
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrl(_("Only http:// and https:// URLs are supported."))
    if not host:
        raise UnsafeUrl(_("The URL has no host."))
    check_remote_host(host, policy)


def check_remote_host(host: str, policy: NetworkPolicy = PUBLIC_INTERNET) -> None:
    """``check_remote_url`` for a protocol that has a server name and no URL:
    refuse a ``localhost`` name or an IP address that is off limits, without
    resolving anything."""
    host = host.strip().lower()
    if host in policy.allowed_hosts:
        return
    if not host:
        raise _unresolved(host)
    if host == "localhost" or host.endswith(".localhost"):
        raise _off_limits(host)
    try:
        address = ip_address(host)
    except ValueError:
        return
    _check_address(address, host, policy)


def connect_vetted(
    host: str,
    port: int,
    timeout,
    policy: NetworkPolicy = PUBLIC_INTERNET,
    source_address=None,
) -> socket.socket:
    """A TCP connection to *host* that only dials an address *policy* accepts:
    ``guarded_transport`` for a client library that is handed its socket.

    The name is resolved once, every answer is vetted before the first is
    dialed, and the addresses that were checked are the ones tried, in the
    resolver's order. Letting the library resolve the name again is what would
    let it answer the check with a public address and the connection with a
    private one. The caller still runs TLS against the name, so SNI and the
    certificate check are those of the server it asked for.
    """
    name = host.strip().lower()
    if name in policy.allowed_hosts:
        return socket.create_connection((host, port), timeout, source_address)
    check_remote_host(name, policy)
    *others, last = _vetted_addresses(name, policy)
    for address in others:
        try:
            return socket.create_connection(
                (str(address), port), timeout, source_address
            )
        except OSError:
            continue
    return socket.create_connection((str(last), port), timeout, source_address)


def check_resolved_url(url: str, policy: NetworkPolicy = PUBLIC_INTERNET) -> None:
    """``check_remote_url``, then resolve the host and refuse it when any
    answer is off limits.

    For a URL accepted now and fetched later, to refuse at once what no fetch
    could reach. It proves nothing about the fetch itself: the name can answer
    differently by then, so the fetch still goes through ``guarded_transport``.
    """
    check_remote_url(url, policy)
    host = urlparse(url).hostname.lower()
    if host not in policy.allowed_hosts:
        _vetted_addresses(host, policy)


def guarded_transport(policy: NetworkPolicy = PUBLIC_INTERNET) -> httpx2.HTTPTransport:
    """An HTTP transport that only connects to addresses *policy* accepts.

    A client given an explicit transport ignores the proxy environment
    variables: a proxy would resolve the name itself, out of the guard's reach.
    """
    transport = httpx2.HTTPTransport()
    # httpx2 has no parameter for it; the pool reads this attribute each time
    # it opens a connection. test_url_guard fails if an upgrade moves it.
    transport._pool._network_backend = _GuardedBackend(policy)
    return transport


def read_capped(response: httpx2.Response, max_bytes: int) -> bytes:
    """The decoded body of a streamed *response*, refused with
    ``ResponseTooLarge`` as soon as it grows past *max_bytes*.

    The count is taken after decompression, so a small gzip bomb is cut off
    as early as a large body.
    """
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > max_bytes:
            raise ResponseTooLarge(max_bytes)
    return bytes(body)


class _GuardedBackend(httpcore2.SyncBackend):
    def __init__(self, policy):
        super().__init__()
        self._policy = policy

    def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        if host.lower() in self._policy.allowed_hosts:
            return super().connect_tcp(
                host, port, timeout, local_address, socket_options
            )
        # Every answer is vetted before the first is dialed, then they are
        # tried in the resolver's order, as socket.create_connection would;
        # the last one's failure is the one raised.
        *others, last = _vetted_addresses(host, self._policy)
        for address in others:
            try:
                return super().connect_tcp(
                    str(address), port, timeout, local_address, socket_options
                )
            except httpcore2.ConnectError, httpcore2.ConnectTimeout:
                continue
        return super().connect_tcp(
            str(last), port, timeout, local_address, socket_options
        )


def _vetted_addresses(host, policy):
    addresses = _resolve(host)
    for address in addresses:
        _check_address(address, host, policy)
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


def _off_limits(host):
    return UnsafeUrl(
        _("'%(host)s' points to an address this server will not contact.")
        % {"host": host}
    )


def _check_address(address, host, policy):
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        raise _off_limits(host)
    # Not-global rather than is_private: it also covers carrier-grade NAT
    # (100.64/10) and other ranges that are routable inside a network but
    # never on the Internet.
    if not address.is_global and not policy.allow_private_networks:
        message = _("'%(host)s' is on a private network.") % {"host": host}
        if policy.private_network_hint:
            message = f"{message} {policy.private_network_hint}"
        raise UnsafeUrl(message)
