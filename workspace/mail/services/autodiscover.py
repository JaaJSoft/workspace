"""IMAP and SMTP settings of an email domain, from its Mozilla autoconfig file.

The format is Thunderbird's (https://wiki.mozilla.org/Thunderbird:Autoconfiguration:ConfigFileFormat):
a provider publishes ``config-v1.1.xml`` on its own domain, and Mozilla's
database covers the large providers that do not.

The domain comes from what a user typed, so everything fetched is treated as
hostile input: the domain must be a plain hostname, the host is checked
against private networks before it is contacted, redirects are not followed,
the body is capped, and the XML is parsed without entities or a DTD. A server
that cannot be reached, answers anything but 200 or sends a malformed file is
skipped, never an error - the caller only needs to know whether a setting was
found.

The host is resolved once by the guard and again by the HTTP client, so a name
answering differently each time (DNS rebinding) can slip past the guard. What
gets back to the caller is a few validated fields of a parsed file, never the
response, which keeps the leak of such a request to its existence.
"""

import logging
import re
import time
from dataclasses import dataclass

import httpx2
from lxml import etree

from workspace.common.logging import scrub
from workspace.imports.services.url_guard import UnsafeUrl, check_remote_url

logger = logging.getLogger(__name__)

# Mozilla's database of the providers that publish no file of their own.
_ISPDB_URL = "https://autoconfig.thunderbird.net/v1.1/{domain}"
_CANDIDATE_URLS = (
    "https://autoconfig.{domain}/mail/config-v1.1.xml",
    "https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml",
    _ISPDB_URL,
)

# Real files weigh a few kilobytes.
_MAX_BYTES = 64 * 1024
# The timeout bounds each read; the deadline bounds the whole response, which
# a server sending a byte at a time could otherwise stretch indefinitely.
_TIMEOUT = httpx2.Timeout(4.0)
_DEADLINE_SECONDS = 8.0
_HEADERS = {"User-Agent": "mail-autoconfig", "Accept": "application/xml, text/xml"}

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"(?=.{{1,253}}$)(?:{_LABEL}\.)+{_LABEL}")
# A file with a DTD can only be trying to declare an entity.
_DTD = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)

# What the socketType of the file means for us; "plain" is left out on purpose.
_SECURITY = {"SSL": "ssl", "STARTTLS": "starttls"}
# The security each protocol can be set up with here. The IMAP connector has
# no STARTTLS mode (imap_use_ssl off is a connection in clear), so an IMAP
# server offered only through STARTTLS would be logged into in clear.
_USABLE_SECURITY = {"imap": {"ssl"}, "smtp": {"ssl", "starttls"}}


@dataclass(frozen=True)
class MailServer:
    host: str
    port: int
    # "ssl": TLS from the first byte; "starttls": upgraded after connecting.
    security: str


@dataclass(frozen=True)
class MailServers:
    imap: MailServer
    smtp: MailServer


def discover(domain):
    """The IMAP and SMTP servers of *domain*, or None when none is published."""
    domain = _normalize(domain)
    if domain is None:
        return None
    for template in _CANDIDATE_URLS:
        url = template.format(domain=domain)
        body = _fetch(url)
        if body is None:
            continue
        servers = _parse(body, domain)
        if servers is not None:
            return servers
        logger.debug("Autoconfig file unusable at %s", scrub(url))
    return None


def _normalize(domain):
    """The lowercase ASCII form of a hostname, or None for anything else.

    A domain is spliced into URLs, so a path, a port, credentials or an IP
    address in it must never get through.
    """
    try:
        domain = (domain or "").strip().rstrip(".").lower().encode("idna").decode()
    except UnicodeError:
        return None
    return domain if _is_hostname(domain) else None


def _is_hostname(value):
    """A dotted name, which an IP address is not: its last label is numeric."""
    return bool(_HOSTNAME.fullmatch(value)) and not value.rsplit(".", 1)[1].isdigit()


def _fetch(url):
    try:
        check_remote_url(url)
    except UnsafeUrl:
        return None
    body = bytearray()
    deadline = time.monotonic() + _DEADLINE_SECONDS
    try:
        with httpx2.Client(
            timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=False
        ) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return None
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > _MAX_BYTES or time.monotonic() > deadline:
                        return None
    except httpx2.HTTPError:
        return None
    return bytes(body)


def _parse(body, domain):
    if _DTD.search(body):
        return None
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
    )
    try:
        root = etree.fromstring(body, parser)
    except etree.XMLSyntaxError:
        return None
    if root.tag != "clientConfig":
        return None
    imap = _first_server(root, "incomingServer", "imap", domain)
    smtp = _first_server(root, "outgoingServer", "smtp", domain)
    if imap is None or smtp is None:
        return None
    return MailServers(imap=imap, smtp=smtp)


def _first_server(root, element, protocol, domain):
    """The first server of *protocol* we can use: files list them by preference."""
    for node in root.iterfind(f"emailProvider/{element}[@type='{protocol}']"):
        # A file may be written once for every domain of a provider.
        host = (
            (node.findtext("hostname") or "").strip().replace("%EMAILDOMAIN%", domain)
        )
        security = _SECURITY.get((node.findtext("socketType") or "").strip())
        try:
            port = int((node.findtext("port") or "").strip())
        except ValueError:
            continue
        if security not in _USABLE_SECURITY[protocol] or not 0 < port < 65536:
            continue
        # A placeholder left in the host is one we cannot fill in.
        if not _is_hostname(host.lower()):
            continue
        return MailServer(host=host.lower(), port=port, security=security)
    return None
