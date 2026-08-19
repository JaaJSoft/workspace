"""Plain WebDAV provider: PROPFIND to list, GET to stream.

Hand-rolled on httpx2 + xml.etree rather than a client library: an import
needs three verbs, the transport must stay under our control for the remote
URL guard, and our own WebDAV server emits the same XML.
"""

import io
import logging
import posixpath
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

import httpx2
from django.conf import settings

from workspace.common.logging import scrub

from .base import (
    KIND_FILES,
    AuthenticationFailed,
    ConnectionFailed,
    Provider,
    ProviderError,
    RemoteEntry,
    RemoteNotFound,
)

logger = logging.getLogger(__name__)

DAV = "{DAV:}"

_LIST_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:"><d:prop>'
    b"<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
    b"<d:getetag/><d:getcontenttype/>"
    b"</d:prop></d:propfind>"
)
_QUOTA_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:"><d:prop>'
    b"<d:resourcetype/><d:quota-used-bytes/><d:quota-available-bytes/>"
    b"</d:prop></d:propfind>"
)

_USER_AGENT = "Workspace-Imports/1.0"


def build_client(connection, *, base_url=None, **kwargs) -> httpx2.Client:
    """HTTP client bound to the connection's credentials and, by default, its
    base URL.

    Redirects are not followed: a redirect could point anywhere, including
    inside the network the URL guard just vetted the original host against.
    """
    return httpx2.Client(
        base_url=base_url or connection.base_url,
        auth=httpx2.BasicAuth(connection.username, connection.get_secret()),
        timeout=settings.IMPORTS_HTTP_TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"},
        **kwargs,
    )


def _raise_for_status(response, remote_path=""):
    code = response.status_code
    if code < 400:
        return
    if code in (401, 403):
        raise AuthenticationFailed("The server rejected the username or password.")
    if code == 404:
        raise RemoteNotFound(f"'{remote_path or '/'}' does not exist on the server.")
    raise ProviderError(f"The server answered with HTTP {code}.")


@contextmanager
def _translate_transport_errors(host):
    try:
        yield
    except httpx2.TimeoutException as exc:
        raise ConnectionFailed(f"{host} did not answer in time.") from exc
    except httpx2.TransportError as exc:
        raise ConnectionFailed(f"Could not reach {host}.") from exc


class _ResponseStream(io.RawIOBase):
    """File-like view over an httpx2 byte iterator - pulls one chunk at a time."""

    def __init__(self, chunks):
        self._chunks = chunks
        self._pending = b""

    def readable(self):
        return True

    def readinto(self, buffer):
        while not self._pending:
            try:
                self._pending = next(self._chunks)
            except StopIteration:
                return 0
        n = min(len(buffer), len(self._pending))
        buffer[:n] = self._pending[:n]
        self._pending = self._pending[n:]
        return n


class WebDavFileSource:
    ROOT_ID = "/"

    def __init__(self, connection, client=None):
        self._connection = connection
        self._client = client or build_client(connection)
        self._base_path = urlparse(connection.base_url).path.rstrip("/")
        self._host = urlparse(connection.base_url).hostname or connection.base_url

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    # -- paths ---------------------------------------------------------

    def _url_for(self, entry_id: str, *, collection=False) -> str:
        # The client's base_url carries the DAV root; entry ids are relative
        # to it. A collection URL ends with "/" so servers don't 301 us.
        path = entry_id.strip("/")
        url = "/" + path if path else "/"
        if collection and not url.endswith("/"):
            url += "/"
        return url

    def _entry_id_from_href(self, href: str) -> str:
        path = unquote(urlparse(href).path)
        if self._base_path and path.startswith(self._base_path):
            path = path[len(self._base_path) :]
        path = "/" + path.strip("/")
        return path

    # -- FileSource ----------------------------------------------------

    def list_dir(self, entry_id: str):
        url = self._url_for(entry_id, collection=True)
        with _translate_transport_errors(self._host):
            response = self._client.request(
                "PROPFIND",
                url,
                content=_LIST_BODY,
                headers={"Depth": "1", "Content-Type": "application/xml"},
            )
        _raise_for_status(response, entry_id)
        if response.status_code != 207:
            raise ProviderError(
                f"'{self._host}' is not a WebDAV server (HTTP {response.status_code} to PROPFIND)."
            )
        requested = "/" + entry_id.strip("/")
        for entry in _parse_multistatus(response.content, self._entry_id_from_href):
            if entry.id == requested:
                continue  # the collection itself comes first in the response
            yield entry

    @contextmanager
    def open(self, entry: RemoteEntry):
        url = self._url_for(entry.id)
        with _translate_transport_errors(self._host):
            with self._client.stream("GET", url) as response:
                _raise_for_status(response, entry.id)
                yield io.BufferedReader(_ResponseStream(response.iter_raw()))

    # -- discovery -----------------------------------------------------

    def probe(self) -> dict:
        """PROPFIND Depth 0 on the root: proves auth and the DAV root, and
        reads the RFC 4331 quota properties when the server exposes them."""
        with _translate_transport_errors(self._host):
            response = self._client.request(
                "PROPFIND",
                self._url_for(self.ROOT_ID, collection=True),
                content=_QUOTA_BODY,
                headers={"Depth": "0", "Content-Type": "application/xml"},
            )
        _raise_for_status(response, "/")
        if response.status_code != 207:
            raise ProviderError(
                f"'{self._host}' is not a WebDAV server (HTTP {response.status_code} to PROPFIND)."
            )
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise ProviderError(
                "The server sent an unreadable WebDAV response."
            ) from exc
        props = _first_ok_props(root)
        if props is None or props.find(f"{DAV}resourcetype/{DAV}collection") is None:
            raise ProviderError("The URL does not point to a WebDAV folder.")
        return {
            "quota_used": _int_or_none(props.findtext(f"{DAV}quota-used-bytes")),
            "quota_available": _int_or_none(
                props.findtext(f"{DAV}quota-available-bytes")
            ),
        }


def _first_ok_props(multistatus):
    for response in multistatus.iter(f"{DAV}response"):
        for propstat in response.findall(f"{DAV}propstat"):
            if " 200 " in (propstat.findtext(f"{DAV}status") or ""):
                return propstat.find(f"{DAV}prop")
    return None


def _parse_multistatus(content: bytes, entry_id_from_href):
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ProviderError("The server sent an unreadable WebDAV response.") from exc
    for response in root.iter(f"{DAV}response"):
        href = response.findtext(f"{DAV}href")
        if not href:
            continue
        props = None
        for propstat in response.findall(f"{DAV}propstat"):
            if " 200 " in (propstat.findtext(f"{DAV}status") or ""):
                props = propstat.find(f"{DAV}prop")
                break
        if props is None:
            continue
        entry_id = entry_id_from_href(href)
        is_dir = props.find(f"{DAV}resourcetype/{DAV}collection") is not None
        yield RemoteEntry(
            id=entry_id,
            name=posixpath.basename(entry_id) or "/",
            is_dir=is_dir,
            size=None
            if is_dir
            else _int_or_none(props.findtext(f"{DAV}getcontentlength")),
            modified_at=_parse_http_date(props.findtext(f"{DAV}getlastmodified")),
            etag=(props.findtext(f"{DAV}getetag") or "").strip(),
            mime_type=""
            if is_dir
            else (props.findtext(f"{DAV}getcontenttype") or "").strip(),
        )


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError, TypeError:
        return None


def _parse_http_date(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except ValueError, TypeError:
        logger.debug("Unparseable getlastmodified %s", scrub(value[:40]))
        return None


class WebDavProvider(Provider):
    slug = "webdav"
    name = "WebDAV"
    kinds = frozenset({KIND_FILES})

    def test_connection(self, connection) -> dict:
        capabilities = {"kinds": sorted(self.kinds)}
        with WebDavFileSource(connection) as source:
            capabilities.update(source.probe())
        return capabilities

    def file_source(self, connection):
        return WebDavFileSource(connection)
