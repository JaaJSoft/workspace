"""CardDAV (RFC 6352) and WebDAV sync (RFC 6578) for the user's address books.

Layout, the one every client discovers from ``/.well-known/carddav``::

    /carddav/                                         root: points at the principal
    /carddav/principals/<username>/                   the user: points at the home
    /carddav/addressbooks/<username>/                 home: one collection per book
    /carddav/addressbooks/<username>/<book>/          an address book
    /carddav/addressbooks/<username>/<book>/<name>    one contact or list

The books are the user's own (``personal``) and one per group they belong to
(``group-<id>``), every one of them writable: the app has no roles inside an
address book. Lists are served as group cards.

Cards are served as vCard 3.0 unless the client asks for 4.0: Apple's
Contacts reads nothing newer, and every other client reads 3.0 too.
"""

from urllib.parse import unquote
from xml.etree import ElementTree as ET

from django.db import IntegrityError
from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.urls import get_script_prefix
from django.views.decorators.csrf import csrf_exempt

from workspace.common.dav.properties import (
    PropertySet,
    hrefs,
    parse_sync_token,
    privilege_set,
    resourcetype,
    supported_report_set,
)
from workspace.common.dav.views import DavView, check_preconditions, propfind_request
from workspace.common.dav.xml import (
    DAV,
    DavError,
    Multistatus,
    element,
    parse_body,
    qname,
    register_prefix,
    requested_properties,
)

from ..queries import reachable_address_book, user_address_books
from ..services.cards import (
    InvalidCard,
    book_cards,
    delete_card,
    find_card,
    render_card,
    store_card,
)
from ..services.sync_log import (
    book_revision,
    changes_since,
    object_etag,
    object_name,
    object_revisions,
)
from ..services.vcard import VCardError, parse_vcards

CARDDAV = "urn:ietf:params:xml:ns:carddav"
CALSERVER = "http://calendarserver.org/ns/"

register_prefix("card", CARDDAV)
register_prefix("cs", CALSERVER)

# The largest card a client may PUT. Photos come inline as base64 and are
# stored downscaled to avatar size, so this only bounds what one request may
# hold in memory: room for a full-resolution phone photo, not for a video.
MAX_RESOURCE_SIZE = 4 * 1024 * 1024

SYNC_TOKEN_PREFIX = "urn:workspace:contacts-sync:"

VERSIONS = ("3.0", "4.0")
DEFAULT_VERSION = "3.0"


def _d(name):
    return qname(DAV, name)


def _card(name):
    return qname(CARDDAV, name)


# ── Paths ─────────────────────────────────────────────────────────────


def _root_path():
    return f"{get_script_prefix()}carddav/"


def _principals_path():
    return f"{_root_path()}principals/"


def _principal_path(username):
    return f"{_principals_path()}{username}/"


def addressbook_home_path(username):
    return f"{_root_path()}addressbooks/{username}/"


def _book_path(username, book):
    return f"{addressbook_home_path(username)}{book.slug}/"


# ── Versions ──────────────────────────────────────────────────────────


def _version_from_accept(request):
    """The vCard version an ``Accept: text/vcard; version=4.0`` header asks for."""
    for media_range in request.headers.get("Accept", "").split(","):
        for param in media_range.split(";")[1:]:
            key, _, value = param.partition("=")
            if key.strip().lower() == "version" and value.strip(' "') in VERSIONS:
                return value.strip(' "')
    return DEFAULT_VERSION


def _version_from_body(root):
    """The version an ``<address-data version="4.0"/>`` element asks for."""
    address_data = (
        root.find(f".//{_card('address-data')}") if root is not None else None
    )
    if address_data is not None and address_data.get("version") in VERSIONS:
        return address_data.get("version")
    return DEFAULT_VERSION


def _content_type(version):
    return f"text/vcard; charset=utf-8; version={version}"


# ── Properties ────────────────────────────────────────────────────────


def _sync_token(revision):
    return f"{SYNC_TOKEN_PREFIX}{revision}"


def _principal_ref(request):
    return hrefs(
        _d("current-user-principal"), _principal_path(request.user.get_username())
    )


def _collection_properties(request, displayname):
    return PropertySet(
        {
            _d("resourcetype"): lambda: resourcetype(_d("collection")),
            _d("displayname"): lambda: element(_d("displayname"), displayname),
            _d("current-user-principal"): lambda: _principal_ref(request),
            _d("principal-collection-set"): lambda: hrefs(
                _d("principal-collection-set"), _principals_path()
            ),
        }
    )


def _principal_properties(request):
    user = request.user
    username = user.get_username()
    return PropertySet(
        {
            _d("resourcetype"): lambda: resourcetype(_d("principal")),
            _d("displayname"): lambda: element(
                _d("displayname"), user.get_full_name() or username
            ),
            _d("current-user-principal"): lambda: _principal_ref(request),
            _d("principal-URL"): lambda: hrefs(
                _d("principal-URL"), _principal_path(username)
            ),
            _d("principal-collection-set"): lambda: hrefs(
                _d("principal-collection-set"), _principals_path()
            ),
            _card("addressbook-home-set"): lambda: hrefs(
                _card("addressbook-home-set"), addressbook_home_path(username)
            ),
            _d("supported-report-set"): supported_report_set,
        }
    )


def _home_properties(request):
    username = request.user.get_username()
    properties = _collection_properties(request, username)
    properties.builders |= {
        _d("owner"): lambda: hrefs(_d("owner"), _principal_path(username)),
        _d("current-user-privilege-set"): lambda: privilege_set(False),
        _d("supported-report-set"): supported_report_set,
    }
    return properties


def _book_properties(request, book, revision):
    builders = {
        _d("resourcetype"): lambda: resourcetype(
            _d("collection"), _card("addressbook")
        ),
        _d("displayname"): lambda: element(_d("displayname"), book.name),
        _card("supported-address-data"): lambda: element(
            _card("supported-address-data"),
            children=[
                ET.Element(
                    _card("address-data-type"),
                    {"content-type": "text/vcard", "version": version},
                )
                for version in VERSIONS
            ],
        ),
        _card("max-resource-size"): lambda: element(
            _card("max-resource-size"), MAX_RESOURCE_SIZE
        ),
        _d("sync-token"): lambda: element(_d("sync-token"), _sync_token(revision)),
        qname(CALSERVER, "getctag"): lambda: element(
            qname(CALSERVER, "getctag"), revision
        ),
        _d("current-user-principal"): lambda: _principal_ref(request),
        _d("current-user-privilege-set"): lambda: privilege_set(True),
        _d("supported-report-set"): lambda: supported_report_set(
            _card("addressbook-multiget"),
            _card("addressbook-query"),
            _d("sync-collection"),
        ),
    }
    if book.owner is not None:
        builders[_d("owner")] = lambda: hrefs(
            _d("owner"), _principal_path(request.user.get_username())
        )
    return PropertySet(builders)


def _object_properties(rendered, etag, version):
    return PropertySet(
        {
            _d("resourcetype"): lambda: element(_d("resourcetype")),
            _d("getetag"): lambda: element(_d("getetag"), etag),
            _d("getcontenttype"): lambda: element(
                _d("getcontenttype"), _content_type(version)
            ),
            _card("address-data"): lambda: element(
                _card("address-data"), rendered().text()
            ),
        },
        expensive=[_card("address-data")],
    )


# ── Views ─────────────────────────────────────────────────────────────


@csrf_exempt
def well_known(request):
    """RFC 6764 discovery: whatever the method, the service lives at /carddav/."""
    return HttpResponsePermanentRedirect(_root_path())


class _CardDavView(DavView):
    dav_compliance = "1, 3, addressbook"

    def _require_self(self, username):
        if username != self.request.user.get_username():
            raise DavError(404)


class CardDavRootView(_CardDavView):
    """``/carddav/`` and ``/carddav/principals/``: where a client asks who it is."""

    def propfind(self, request):
        _level, mode, requested = propfind_request(request)
        multistatus = Multistatus()
        _collection_properties(request, "CardDAV").add_to(
            multistatus, request.path, mode, requested
        )
        return multistatus.response()


class PrincipalView(_CardDavView):
    def propfind(self, request, username):
        self._require_self(username)
        _level, mode, requested = propfind_request(request)
        multistatus = Multistatus()
        _principal_properties(request).add_to(
            multistatus, _principal_path(username), mode, requested
        )
        return multistatus.response()

    def report(self, request, username):
        raise DavError(403, _d("supported-report"))


class AddressBookHomeView(_CardDavView):
    def propfind(self, request, username):
        self._require_self(username)
        level, mode, requested = propfind_request(request)
        multistatus = Multistatus()
        _home_properties(request).add_to(
            multistatus, addressbook_home_path(username), mode, requested
        )
        if level == "1":
            for book in user_address_books(request.user):
                _book_properties(request, book, _revision(book)).add_to(
                    multistatus, _book_path(username, book), mode, requested
                )
        return multistatus.response()

    def report(self, request, username):
        raise DavError(403, _d("supported-report"))


def _book_ids(book):
    return getattr(book.owner, "pk", None), getattr(book.group, "pk", None)


def _revision(book):
    return book_revision(*_book_ids(book))


def _reachable_book(request, username, slug):
    if username != request.user.get_username():
        raise DavError(404)
    book = reachable_address_book(request.user, slug)
    if book is None:
        raise DavError(404)
    return book


def _etags(book, cards):
    revisions = object_revisions(*_book_ids(book), [card.pk for card in cards])
    return {card.pk: object_etag(card.pk, revisions.get(card.pk, 0)) for card in cards}


class _Lazy:
    """Render a card on first use only, then reuse the result."""

    def __init__(self, card, version):
        self.card = card
        self.version = version
        self.rendered = None

    def __call__(self):
        if self.rendered is None:
            self.rendered = render_card(self.card, self.version)
        return self.rendered


def _add_cards(multistatus, book_path, book, cards, mode, requested, version):
    """One ``<response>`` per card; *cards* may be ``_Lazy`` renderers already."""
    lazies = [c if isinstance(c, _Lazy) else _Lazy(c, version) for c in cards]
    etags = _etags(book, [lazy.card for lazy in lazies])
    for lazy in lazies:
        _object_properties(lazy, etags[lazy.card.pk], version).add_to(
            multistatus, f"{book_path}{object_name(lazy.card)}", mode, requested
        )


class AddressBookView(_CardDavView):
    def propfind(self, request, username, slug):
        book = _reachable_book(request, username, slug)
        level, mode, requested = propfind_request(request)
        path = _book_path(username, book)
        multistatus = Multistatus()
        _book_properties(request, book, _revision(book)).add_to(
            multistatus, path, mode, requested
        )
        if level == "1":
            version = _version_from_body(parse_body(request.body))
            _add_cards(
                multistatus, path, book, book_cards(book), mode, requested, version
            )
        return multistatus.response()

    def proppatch(self, request, username, slug):
        # Names come from the user's account and groups, not from a client.
        _reachable_book(request, username, slug)
        root = parse_body(request.body)
        if root is None or root.tag != _d("propertyupdate"):
            raise DavError(400, message="Expected a propertyupdate body.")
        refused = [
            child.tag
            for action in root
            for prop in action.iterfind(_d("prop"))
            for child in prop
        ]
        multistatus = Multistatus()
        multistatus.add_propstat(request.path, forbidden=refused)
        return multistatus.response()

    def report(self, request, username, slug):
        book = _reachable_book(request, username, slug)
        root = parse_body(request.body)
        if root is None:
            raise DavError(400, message="Expected a REPORT body.")
        path = _book_path(username, book)
        mode, requested = requested_properties(root)
        version = _version_from_body(root)
        if root.tag == _card("addressbook-multiget"):
            return self._multiget(book, path, root, mode, requested, version)
        if root.tag == _card("addressbook-query"):
            return self._query(book, path, root, mode, requested, version)
        if root.tag == _d("sync-collection"):
            return self._sync(book, path, root, mode, requested, version)
        raise DavError(403, _d("supported-report"))

    def delete(self, request, username, slug):
        _reachable_book(request, username, slug)
        raise DavError(403, message="Address books cannot be deleted.")

    def _multiget(self, book, path, root, mode, requested, version):
        cards, missing = [], []
        for node in root.iterfind(_d("href")):
            name = unquote((node.text or "").strip().rstrip("/").rsplit("/", 1)[-1])
            card = find_card(book, name) if name else None
            if card is None:
                missing.append(f"{path}{name}")
            else:
                cards.append(card)
        multistatus = Multistatus()
        _add_cards(multistatus, path, book, cards, mode, requested, version)
        for href in missing:
            multistatus.add_status(href, 404)
        return multistatus.response()

    def _query(self, book, path, root, mode, requested, version):
        card_filter = root.find(_card("filter"))
        lazies = [_Lazy(card, version) for card in book_cards(book)]
        if card_filter is not None:
            lazies = [
                lazy for lazy in lazies if _matches(lazy().component, card_filter)
            ]
        limit = (root.findtext(f"{_card('limit')}/{_card('nresults')}") or "").strip()
        if limit.isdigit():
            lazies = lazies[: int(limit)]
        multistatus = Multistatus()
        _add_cards(multistatus, path, book, lazies, mode, requested, version)
        return multistatus.response()

    def _sync(self, book, path, root, mode, requested, version):
        level = (root.findtext(_d("sync-level")) or "1").strip()
        if level != "1":
            raise DavError(403, _d("sync-traversal-supported"))
        owner_id, group_id = _book_ids(book)
        current = _revision(book)
        revision = parse_sync_token(
            (root.findtext(_d("sync-token")) or "").strip(), SYNC_TOKEN_PREFIX, current
        )

        multistatus = Multistatus()
        if revision == 0:
            cards = book_cards(book)
            gone = []
        else:
            changes = list(changes_since(owner_id, group_id, revision))
            changed = {change.object_uuid for change in changes}
            present = {card.pk: card for card in book_cards(book) if card.pk in changed}
            cards = [
                present[c.object_uuid] for c in changes if c.object_uuid in present
            ]
            gone = [c.name for c in changes if c.object_uuid not in present]
        _add_cards(multistatus, path, book, cards, mode, requested, version)
        for name in gone:
            multistatus.add_status(f"{path}{name}", 404)
        multistatus.append(element(_d("sync-token"), _sync_token(current)))
        return multistatus.response()


# ── addressbook-query filters (RFC 6352 10.5) ─────────────────────────


def _matches(component, card_filter):
    prop_filters = list(card_filter.iterfind(_card("prop-filter")))
    if not prop_filters:
        return True
    results = (_prop_matches(component, prop_filter) for prop_filter in prop_filters)
    return all(results) if card_filter.get("test") == "allof" else any(results)


def _prop_matches(component, prop_filter):
    lines = component.contents.get(prop_filter.get("name", "").lower(), [])
    if prop_filter.find(_card("is-not-defined")) is not None:
        return not lines
    if not lines:
        return False
    tests = [
        any(_text_matches(_line_text(line), text_match) for line in lines)
        for text_match in prop_filter.iterfind(_card("text-match"))
    ] + [
        any(_param_matches(line, param_filter) for line in lines)
        for param_filter in prop_filter.iterfind(_card("param-filter"))
    ]
    if not tests:
        return True
    return all(tests) if prop_filter.get("test") == "allof" else any(tests)


def _line_text(line):
    value = line.value
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)


def _text_matches(text, text_match):
    needle = text_match.text or ""
    if text_match.get("collation", "i;unicode-casemap") != "i;octet":
        text, needle = text.casefold(), needle.casefold()
    match_type = text_match.get("match-type", "contains")
    if match_type == "equals":
        result = text == needle
    elif match_type == "starts-with":
        result = text.startswith(needle)
    elif match_type == "ends-with":
        result = text.endswith(needle)
    else:
        result = needle in text
    return result != (text_match.get("negate-condition") == "yes")


def _param_matches(line, param_filter):
    values = line.params.get(param_filter.get("name", "").upper(), [])
    if param_filter.find(_card("is-not-defined")) is not None:
        return not values
    text_match = param_filter.find(_card("text-match"))
    if text_match is None:
        return bool(values)
    return any(_text_matches(str(value), text_match) for value in values)


class CardView(_CardDavView):
    def _resolve(self, request, username, slug, name):
        book = _reachable_book(request, username, slug)
        card = find_card(book, name)
        etag = _etags(book, [card])[card.pk] if card is not None else None
        return book, card, etag

    def get(self, request, username, slug, name):
        _book, card, etag = self._resolve(request, username, slug, name)
        if card is None:
            raise DavError(404)
        version = _version_from_accept(request)
        response = HttpResponse(
            render_card(card, version).text(), content_type=_content_type(version)
        )
        response["ETag"] = etag
        return response

    def propfind(self, request, username, slug, name):
        book, card, etag = self._resolve(request, username, slug, name)
        if card is None:
            raise DavError(404)
        _level, mode, requested = propfind_request(request)
        version = _version_from_body(parse_body(request.body))
        multistatus = Multistatus()
        _object_properties(_Lazy(card, version), etag, version).add_to(
            multistatus, f"{_book_path(username, book)}{name}", mode, requested
        )
        return multistatus.response()

    def put(self, request, username, slug, name):
        book, card, etag = self._resolve(request, username, slug, name)
        if len(name) > 255:
            raise DavError(400, message="Resource name too long.")
        length = request.META.get("CONTENT_LENGTH") or "0"
        if not length.isdigit() or int(length) > MAX_RESOURCE_SIZE:
            raise DavError(403, _card("max-resource-size"))
        check_preconditions(request, etag)
        if len(request.body) > MAX_RESOURCE_SIZE:
            raise DavError(403, _card("max-resource-size"))
        parsed = _parse_one_card(request.body)
        try:
            store_card(book, name, parsed, card)
        except InvalidCard as exc:
            status = 409 if exc.condition == "no-uid-conflict" else 403
            raise DavError(status, _card(exc.condition), str(exc)) from None
        except IntegrityError:
            # Another request created the same name in the meantime: the
            # precondition this one was checked against no longer holds.
            raise DavError(412) from None
        # No ETag: the stored card is a re-rendering of what was sent, not
        # the same bytes, and a client must fetch it to learn its ETag.
        return HttpResponse(status=201 if card is None else 204)

    def delete(self, request, username, slug, name):
        _book, card, etag = self._resolve(request, username, slug, name)
        if card is None:
            raise DavError(404)
        check_preconditions(request, etag)
        delete_card(card)
        return HttpResponse(status=204)


def _parse_one_card(body):
    try:
        cards = parse_vcards(body.decode("utf-8"))
    except UnicodeDecodeError, VCardError:
        raise DavError(403, _card("valid-address-data"), "Not a vCard.") from None
    if len(cards) != 1:
        raise DavError(
            403, _card("valid-address-data"), "A resource holds exactly one card."
        )
    return cards[0]
