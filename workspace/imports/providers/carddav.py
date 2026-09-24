"""CardDAV (RFC 6352) read access: the address books of a user, the cards of
a book with their etags, and the cards themselves, fetched in batches with an
addressbook-multiget REPORT.

Built on the WebDAV plumbing - same client (credentials, redirects refused),
same multistatus parsing. The provider hands it the user's address book home.
"""

import logging
import posixpath
import re
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree

import httpx2

from workspace.common.logging import scrub

from ..errors import ImportsError
from ..services.url_guard import check_remote_url
from .base import ProviderError, RemoteAddressBook, RemoteCard
from .webdav import (
    DAV,
    _raise_for_status,
    _translate_transport_errors,
    build_client,
    entry_id_from_href,
    ok_props,
    parse_dav_xml,
)

logger = logging.getLogger(__name__)

CARDDAV = "{urn:ietf:params:xml:ns:carddav}"

# A linked photo past this size is not an avatar, and is not read to the end.
_MAX_PHOTO_BYTES = 5 * 1024 * 1024
_IMAGE_TYPE_RE = re.compile(r"^image/([a-z0-9.+-]+)$")
_DEFAULT_PORTS = {"http": 80, "https": 443}

_BOOKS_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
    b"<d:prop><d:resourcetype/><d:displayname/><card:addressbook-description/>"
    b"</d:prop></d:propfind>"
)
_CARDS_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:"><d:prop>'
    b"<d:resourcetype/><d:getetag/>"
    b"</d:prop></d:propfind>"
)


def _origin(url):
    """``(scheme, host, port)`` with the default port filled in, or None."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    try:
        port = parsed.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    return scheme, parsed.hostname, port


def _collection_url(entry_id):
    # Relative to the client's base URL. Ids are decoded paths, so they are
    # percent-encoded in full, as WebDavFileSource._url_for does.
    path = quote(entry_id.strip("/"), safe="/")
    return f"/{path}/" if path else "/"


class CardDavSource:
    def __init__(self, connection, root_url, client=None, hidden_prefixes=()):
        self.root_url = root_url.rstrip("/") + "/"
        self._base_path = unquote(urlparse(self.root_url).path).rstrip("/")
        self._origin = _origin(self.root_url)
        self._host = urlparse(self.root_url).hostname or self.root_url
        self._client = client or build_client(connection, base_url=self.root_url)
        self._hidden_prefixes = hidden_prefixes

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    # -- ContactSource -------------------------------------------------

    def address_books(self):
        response = self._request("PROPFIND", "/", _BOOKS_BODY, depth="1")
        for href, props in self._members(response):
            if props.find(f"{DAV}resourcetype/{CARDDAV}addressbook") is None:
                continue
            book_id = self._id(href)
            segment = posixpath.basename(book_id)
            if segment.startswith(self._hidden_prefixes):
                continue
            name = (props.findtext(f"{DAV}displayname") or "").strip()
            yield RemoteAddressBook(
                id=book_id,
                name=name or segment or book_id,
                description=(
                    props.findtext(f"{CARDDAV}addressbook-description") or ""
                ).strip(),
            )

    def card_refs(self, book_id):
        response = self._request(
            "PROPFIND", _collection_url(book_id), _CARDS_BODY, depth="1"
        )
        for href, props in self._members(response):
            if props.find(f"{DAV}resourcetype/{DAV}collection") is not None:
                continue  # the book itself
            yield self._id(href), (props.findtext(f"{DAV}getetag") or "").strip()

    def fetch_cards(self, book_id, card_ids):
        if not card_ids:
            return
        response = self._request(
            "REPORT",
            _collection_url(book_id),
            self._multiget_body(card_ids),
            depth="1",
        )
        for href, props in self._members(response):
            text = props.findtext(f"{CARDDAV}address-data")
            if not text:
                continue
            yield RemoteCard(
                id=self._id(href),
                etag=(props.findtext(f"{DAV}getetag") or "").strip(),
                text=text,
            )

    def fetch_photo(self, url):
        # The client sends the connection's credentials to whatever URL it is
        # given, so a card must not be able to name another origin. The
        # comparison itself can raise on a malformed URL, so it stays inside
        # the guarded region: any failure here just means no photo.
        try:
            if _origin(url) != self._origin:
                return None
            check_remote_url(url)
            with (
                _translate_transport_errors(self._host),
                self._client.stream("GET", url) as response,
            ):
                if response.status_code != 200:
                    return None
                content_type = response.headers.get("content-type", "")
                match = _IMAGE_TYPE_RE.match(content_type.split(";")[0].strip().lower())
                if match is None:
                    return None
                data = bytearray()
                for chunk in response.iter_bytes():
                    data += chunk
                    if len(data) > _MAX_PHOTO_BYTES:
                        return None
        except ValueError:
            return None
        except (ImportsError, httpx2.HTTPError) as exc:
            logger.info(
                "Contact photo %s skipped: %s", scrub(url[:200]), scrub(str(exc))
            )
            return None
        return bytes(data), match.group(1)

    # -- internals -----------------------------------------------------

    def _id(self, href):
        return entry_id_from_href(href, self._base_path)

    def _members(self, response):
        """``(href, ok props)`` of every member the server answered 200 for."""
        for element in parse_dav_xml(response.content).iter(f"{DAV}response"):
            href = element.findtext(f"{DAV}href")
            props = ok_props(element)
            if href and props is not None:
                yield href, props

    def _multiget_body(self, card_ids):
        root = ElementTree.Element(f"{CARDDAV}addressbook-multiget")
        prop = ElementTree.SubElement(root, f"{DAV}prop")
        ElementTree.SubElement(prop, f"{DAV}getetag")
        ElementTree.SubElement(prop, f"{CARDDAV}address-data")
        for card_id in card_ids:
            href = ElementTree.SubElement(root, f"{DAV}href")
            href.text = quote(self._base_path + card_id, safe="/")
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    def _request(self, method, url, body, *, depth):
        with _translate_transport_errors(self._host):
            response = self._client.request(
                method,
                url,
                content=body,
                headers={"Depth": depth, "Content-Type": "application/xml"},
            )
        _raise_for_status(response, url)
        if response.status_code != 207:
            raise ProviderError(
                f"'{self._host}' answered HTTP {response.status_code} to "
                f"{method} {url} instead of a multistatus."
            )
        return response
