from unittest.mock import patch
from xml.etree import ElementTree

import httpx2
from django.test import SimpleTestCase

from workspace.imports.models import ImportConnection
from workspace.imports.providers.base import (
    AuthenticationFailed,
    Provider,
    ProviderError,
    RemoteAddressBook,
)
from workspace.imports.providers.carddav import CardDavSource
from workspace.imports.providers.webdav import build_client
from workspace.imports.services.url_guard import UnsafeUrl

ROOT = "https://cloud.example.org/remote.php/dav/addressbooks/users/alice/"
HOME = "/remote.php/dav/addressbooks/users/alice"
GUARD = "workspace.imports.providers.carddav.check_remote_url"
BOOK = "<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype>"
COLLECTION = "<d:resourcetype><d:collection/></d:resourcetype>"
VCARD = "BEGIN:VCARD\nVERSION:3.0\nUID:a\nFN:Ann\nEND:VCARD\n"


def _connection():
    conn = ImportConnection(
        provider="nextcloud",
        label="nc",
        base_url="https://cloud.example.org/remote.php/dav/files/alice",
        username="alice",
    )
    conn.set_secret("pw")
    return conn


def _source(handler, **kwargs):
    conn = _connection()
    client = build_client(conn, base_url=ROOT, transport=httpx2.MockTransport(handler))
    return CardDavSource(conn, ROOT, client=client, **kwargs)


def _response(href, props, status="HTTP/1.1 200 OK"):
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop>"
        f"<d:status>{status}</d:status></d:propstat></d:response>"
    )


def _multistatus(*responses):
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
        'xmlns:card="urn:ietf:params:xml:ns:carddav">'
        + "".join(responses)
        + "</d:multistatus>"
    ).encode()


def _image(content, content_type="image/png"):
    def handler(request):
        return httpx2.Response(
            200, content=content, headers={"Content-Type": content_type}
        )

    return handler


def _never(request):
    raise AssertionError("the server must not be contacted")


class ProviderContractTests(SimpleTestCase):
    def test_a_provider_without_contacts_says_so(self):
        class FilesOnly(Provider):
            slug = "files-only"
            name = "Files only"

            def test_connection(self, connection):
                return {}

        with self.assertRaisesMessage(
            NotImplementedError, "files-only does not provide contacts"
        ):
            FilesOnly().contact_source(None)

    def test_an_address_book_serialises_for_the_api(self):
        book = RemoteAddressBook(id="/contacts", name="Contacts", description="Mine")
        self.assertEqual(
            book.as_dict(),
            {"id": "/contacts", "name": "Contacts", "description": "Mine"},
        )


class AddressBookTests(SimpleTestCase):
    def test_lists_only_address_book_collections(self):
        seen = {}

        def handler(request):
            seen.update(
                method=request.method,
                url=str(request.url),
                depth=request.headers["depth"],
            )
            return httpx2.Response(
                207,
                content=_multistatus(
                    _response(f"{HOME}/", COLLECTION),
                    _response(
                        f"{HOME}/contacts/",
                        BOOK + "<d:displayname>Contacts</d:displayname>",
                    ),
                    _response(
                        f"{HOME}/work%20book/",
                        BOOK
                        + "<card:addressbook-description>Shared by Bob"
                        + "</card:addressbook-description>",
                    ),
                    _response(f"{HOME}/inbox/", COLLECTION),
                ),
            )

        with _source(handler) as source:
            books = list(source.address_books())
        self.assertEqual(
            books,
            [
                RemoteAddressBook(id="/contacts", name="Contacts"),
                RemoteAddressBook(
                    id="/work book", name="work book", description="Shared by Bob"
                ),
            ],
        )
        self.assertEqual(seen, {"method": "PROPFIND", "url": ROOT, "depth": "1"})

    def test_a_rejected_password_is_an_authentication_failure(self):
        with _source(lambda request: httpx2.Response(401)) as source:
            with self.assertRaises(AuthenticationFailed):
                list(source.address_books())

    def test_an_answer_that_is_not_a_multistatus_is_a_provider_error(self):
        with _source(
            lambda request: httpx2.Response(200, content=b"<html/>")
        ) as source:
            with self.assertRaises(ProviderError):
                list(source.address_books())

    def test_a_hidden_prefix_book_is_skipped(self):
        def handler(request):
            return httpx2.Response(
                207,
                content=_multistatus(
                    _response(
                        f"{HOME}/contacts/",
                        BOOK + "<d:displayname>Contacts</d:displayname>",
                    ),
                    _response(f"{HOME}/z-server-generated--system/", BOOK),
                    _response(f"{HOME}/z-app-generated--contactsinteraction/", BOOK),
                ),
            )

        with _source(
            handler, hidden_prefixes=("z-server-generated--", "z-app-generated--")
        ) as source:
            books = list(source.address_books())
        self.assertEqual(books, [RemoteAddressBook(id="/contacts", name="Contacts")])


class CardTests(SimpleTestCase):
    def test_card_refs_list_the_cards_of_a_book_with_their_etags(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx2.Response(
                207,
                content=_multistatus(
                    _response(f"{HOME}/contacts/", COLLECTION),
                    _response(f"{HOME}/contacts/a.vcf", '<d:getetag>"e1"</d:getetag>'),
                    _response(
                        f"{HOME}/contacts/b%40c.vcf", '<d:getetag>"e2"</d:getetag>'
                    ),
                ),
            )

        with _source(handler) as source:
            refs = list(source.card_refs("/contacts"))
        self.assertEqual(
            refs, [("/contacts/a.vcf", '"e1"'), ("/contacts/b@c.vcf", '"e2"')]
        )
        self.assertEqual(seen["url"], ROOT + "contacts/")

    def test_fetch_cards_asks_for_the_hrefs_and_skips_the_missing_ones(self):
        seen = {}

        def handler(request):
            seen["method"] = request.method
            body = ElementTree.fromstring(request.content)
            seen["hrefs"] = [h.text for h in body.iter("{DAV:}href")]
            return httpx2.Response(
                207,
                content=_multistatus(
                    _response(
                        f"{HOME}/contacts/a.vcf",
                        '<d:getetag>"e1"</d:getetag>'
                        f"<card:address-data>{VCARD}</card:address-data>",
                    ),
                    _response(
                        f"{HOME}/contacts/b%20c.vcf",
                        "<d:getetag/>",
                        status="HTTP/1.1 404 Not Found",
                    ),
                ),
            )

        with _source(handler) as source:
            cards = list(
                source.fetch_cards(
                    "/contacts", ["/contacts/a.vcf", "/contacts/b c.vcf"]
                )
            )
        self.assertEqual(seen["method"], "REPORT")
        self.assertEqual(
            seen["hrefs"], [f"{HOME}/contacts/a.vcf", f"{HOME}/contacts/b%20c.vcf"]
        )
        self.assertEqual([(c.id, c.etag) for c in cards], [("/contacts/a.vcf", '"e1"')])
        self.assertIn("FN:Ann", cards[0].text)

    def test_fetching_no_card_sends_no_request(self):
        with _source(_never) as source:
            self.assertEqual(list(source.fetch_cards("/contacts", [])), [])


@patch(GUARD)
class PhotoTests(SimpleTestCase):
    PHOTO = f"https://cloud.example.org{HOME}/contacts/a.vcf?photo"

    def test_a_photo_on_the_same_host_is_fetched_with_the_credentials(self, guard):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization", "")
            return httpx2.Response(
                200, content=b"img", headers={"Content-Type": "image/JPEG; q=1"}
            )

        with _source(handler) as source:
            self.assertEqual(source.fetch_photo(self.PHOTO), (b"img", "jpeg"))
        self.assertEqual(seen["url"], self.PHOTO)
        self.assertTrue(seen["auth"].startswith("Basic "))
        guard.assert_called_once_with(self.PHOTO)

    def test_the_default_port_spelled_out_is_the_same_host(self, guard):
        with _source(_image(b"img")) as source:
            self.assertEqual(
                source.fetch_photo("https://cloud.example.org:443/p.png"),
                (b"img", "png"),
            )

    def test_a_photo_anywhere_else_is_never_requested(self, guard):
        with _source(_never) as source:
            for url in (
                "https://tracker.example.com/p.png",
                "http://cloud.example.org/p.png",
                "https://cloud.example.org:8443/p.png",
                "https://cloud.example.org:notaport/p.png",
            ):
                with self.subTest(url=url):
                    self.assertIsNone(source.fetch_photo(url))
        guard.assert_not_called()

    def test_anything_but_an_image_is_refused(self, guard):
        with _source(_image(b"<html/>", "text/html")) as source:
            self.assertIsNone(source.fetch_photo("https://cloud.example.org/p"))

    def test_an_error_status_is_no_photo(self, guard):
        with _source(lambda request: httpx2.Response(404)) as source:
            self.assertIsNone(source.fetch_photo("https://cloud.example.org/p"))

    def test_an_oversized_photo_is_dropped(self, guard):
        with patch("workspace.imports.providers.carddav._MAX_PHOTO_BYTES", 4):
            with _source(_image(b"12345")) as source:
                self.assertIsNone(source.fetch_photo("https://cloud.example.org/p.png"))

    def test_a_host_the_guard_refuses_is_no_photo(self, guard):
        guard.side_effect = UnsafeUrl("private")
        with _source(_never) as source:
            self.assertIsNone(source.fetch_photo("https://cloud.example.org/p.png"))

    def test_a_transport_failure_is_no_photo(self, guard):
        def handler(request):
            raise httpx2.ConnectError("down")

        with _source(handler) as source:
            self.assertIsNone(source.fetch_photo("https://cloud.example.org/p.png"))

    def test_a_malformed_url_is_no_photo(self, guard):
        with _source(_never) as source:
            self.assertIsNone(source.fetch_photo("https://[cloud.example.org/p.png"))
        guard.assert_not_called()

    def test_a_corrupt_encoded_body_is_no_photo(self, guard):
        def handler(request):
            return httpx2.Response(
                200,
                content=b"not actually gzip",
                headers={"Content-Type": "image/png", "Content-Encoding": "gzip"},
            )

        with _source(handler) as source:
            self.assertIsNone(source.fetch_photo("https://cloud.example.org/p.png"))
