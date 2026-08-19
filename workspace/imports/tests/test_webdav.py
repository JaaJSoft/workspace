import io
import shutil
import tempfile
from datetime import UTC, datetime
from unittest.mock import patch

import httpx2
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings

from workspace.files.services import FileService
from workspace.imports.models import ImportConnection
from workspace.imports.providers.base import (
    AuthenticationFailed,
    ConnectionFailed,
    ProviderError,
    RemoteEntry,
    RemoteNotFound,
)
from workspace.imports.providers.webdav import (
    WebDavFileSource,
    WebDavProvider,
    _parse_multistatus,
    _ResponseStream,
    build_client,
)

User = get_user_model()

NEXTCLOUD_MULTISTATUS = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns" xmlns:oc="http://owncloud.org/ns">
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:getlastmodified>Tue, 19 Aug 2026 08:00:00 GMT</d:getlastmodified>
        <d:getetag>&quot;68a3f1c2a1b2c&quot;</d:getetag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
    <d:propstat>
      <d:prop><d:getcontentlength/><d:getcontenttype/></d:prop>
      <d:status>HTTP/1.1 404 Not Found</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/Rapport%20final.pdf</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>20480</d:getcontentlength>
        <d:getlastmodified>Mon, 18 Aug 2026 10:30:00 GMT</d:getlastmodified>
        <d:getetag>&quot;abc123&quot;</d:getetag>
        <d:getcontenttype>application/pdf</d:getcontenttype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/Photos%20%C3%A9t%C3%A9/</d:href>
    <d:propstat>
      <d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/broken</d:href>
    <d:propstat>
      <d:prop><d:resourcetype/></d:prop>
      <d:status>HTTP/1.1 403 Forbidden</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


def _connection(
    owner=None, base_url="https://cloud.example.org/remote.php/dav/files/alice"
):
    conn = ImportConnection(
        owner=owner, provider="webdav", label="t", base_url=base_url, username="alice"
    )
    conn.set_secret("pw")
    return conn


class MultistatusParsingTests(SimpleTestCase):
    def setUp(self):
        self.source = WebDavFileSource(_connection(), client=object())

    def test_entries_are_relative_to_the_dav_root_and_decoded(self):
        entries = list(
            _parse_multistatus(NEXTCLOUD_MULTISTATUS, self.source._entry_id_from_href)
        )
        self.assertEqual(
            [e.id for e in entries],
            ["/Documents", "/Documents/Rapport final.pdf", "/Documents/Photos été"],
        )
        self.assertEqual(entries[2].name, "Photos été")
        self.assertTrue(entries[2].is_dir)

    def test_file_properties(self):
        entries = list(
            _parse_multistatus(NEXTCLOUD_MULTISTATUS, self.source._entry_id_from_href)
        )
        pdf = entries[1]
        self.assertFalse(pdf.is_dir)
        self.assertEqual(pdf.size, 20480)
        self.assertEqual(pdf.etag, '"abc123"')
        self.assertEqual(pdf.mime_type, "application/pdf")
        self.assertEqual(pdf.modified_at, datetime(2026, 8, 18, 10, 30, tzinfo=UTC))

    def test_directory_has_no_size_and_no_mime(self):
        entries = list(
            _parse_multistatus(NEXTCLOUD_MULTISTATUS, self.source._entry_id_from_href)
        )
        self.assertIsNone(entries[0].size)
        self.assertEqual(entries[0].mime_type, "")

    def test_entries_without_a_200_propstat_are_dropped(self):
        entries = list(
            _parse_multistatus(NEXTCLOUD_MULTISTATUS, self.source._entry_id_from_href)
        )
        self.assertNotIn("/Documents/broken", [e.id for e in entries])

    def test_garbage_is_a_provider_error(self):
        with self.assertRaises(ProviderError):
            list(_parse_multistatus(b"<html>nope", self.source._entry_id_from_href))

    def test_list_dir_skips_the_collection_itself(self):
        transport = httpx2.MockTransport(
            lambda request: httpx2.Response(207, content=NEXTCLOUD_MULTISTATUS)
        )
        conn = _connection()
        source = WebDavFileSource(conn, client=build_client(conn, transport=transport))
        ids = [e.id for e in source.list_dir("/Documents")]
        self.assertEqual(ids, ["/Documents/Rapport final.pdf", "/Documents/Photos été"])

    def test_list_dir_requests_depth_one_on_a_collection_url(self):
        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["depth"] = request.headers["Depth"]
            return httpx2.Response(207, content=NEXTCLOUD_MULTISTATUS)

        conn = _connection()
        source = WebDavFileSource(
            conn, client=build_client(conn, transport=httpx2.MockTransport(handler))
        )
        list(source.list_dir("/Documents"))
        self.assertEqual(seen["method"], "PROPFIND")
        self.assertEqual(seen["depth"], "1")
        self.assertEqual(
            seen["url"],
            "https://cloud.example.org/remote.php/dav/files/alice/Documents/",
        )


class ErrorTranslationTests(SimpleTestCase):
    def _source(self, handler):
        conn = _connection()
        return WebDavFileSource(
            conn, client=build_client(conn, transport=httpx2.MockTransport(handler))
        )

    def test_401_is_authentication_failed(self):
        source = self._source(lambda r: httpx2.Response(401))
        with self.assertRaises(AuthenticationFailed):
            list(source.list_dir("/"))

    def test_404_is_remote_not_found(self):
        source = self._source(lambda r: httpx2.Response(404))
        with self.assertRaises(RemoteNotFound):
            list(source.list_dir("/missing"))

    def test_html_page_is_not_a_dav_server(self):
        source = self._source(lambda r: httpx2.Response(200, text="<html/>"))
        with self.assertRaisesRegex(ProviderError, "not a WebDAV server"):
            list(source.list_dir("/"))

    def test_redirect_on_get_is_not_the_file(self):
        source = self._source(
            lambda r: httpx2.Response(302, headers={"Location": "https://elsewhere/"})
        )
        entry = RemoteEntry(id="/a.txt", name="a.txt", is_dir=False)
        with self.assertRaisesRegex(ProviderError, "HTTP 302"):
            with source.open(entry):
                pass

    def test_transport_errors_become_connection_failed(self):
        def handler(request):
            raise httpx2.ConnectError("boom")

        with self.assertRaisesRegex(ConnectionFailed, "Could not reach"):
            list(self._source(handler).list_dir("/"))

    def test_timeouts_become_connection_failed(self):
        def handler(request):
            raise httpx2.ReadTimeout("slow")

        with self.assertRaisesRegex(ConnectionFailed, "did not answer in time"):
            self._source(handler).probe()

    def test_probe_requires_a_collection(self):
        body = (
            b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/x</d:href>'
            b"<d:propstat><d:prop><d:resourcetype/></d:prop>"
            b"<d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
            b"</d:response></d:multistatus>"
        )
        source = self._source(lambda r: httpx2.Response(207, content=body))
        with self.assertRaisesRegex(ProviderError, "does not point to a WebDAV folder"):
            source.probe()


class ResponseStreamTests(SimpleTestCase):
    def test_reads_across_chunk_boundaries(self):
        reader = io.BufferedReader(_ResponseStream(iter([b"abc", b"", b"defg", b"h"])))
        self.assertEqual(reader.read(2), b"ab")
        self.assertEqual(reader.read(), b"cdefgh")
        self.assertEqual(reader.read(), b"")


@override_settings(IMPORTS_HTTP_TIMEOUT=5)
class AgainstOurOwnServerTests(TestCase):
    """End to end: import from another user's WebDAV space served by our own
    WSGI entry point (Django + WsgiDAV dispatch on /dav), through httpx2's WSGI
    transport - no network involved."""

    def setUp(self):
        from workspace.wsgi import application

        self._tmpdir = tempfile.mkdtemp()
        self._media = override_settings(MEDIA_ROOT=self._tmpdir)
        self._media.enable()
        self.remote_user = User.objects.create_user(username="bob", password="bobpw")
        docs = FileService.create_folder(self.remote_user, "Documents")
        FileService.create_file(
            self.remote_user, "notes.txt", docs, content=ContentFile(b"hello import")
        )
        FileService.create_file(
            self.remote_user, "root.md", None, content=ContentFile(b"# root")
        )
        self.transport = httpx2.WSGITransport(app=application)
        self.conn = ImportConnection(
            owner=self.remote_user,
            provider="webdav",
            label="bob",
            base_url="http://testserver/dav",
            username="bob",
        )
        self.conn.set_secret("bobpw")

    def tearDown(self):
        self._media.disable()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _source(self, conn=None):
        conn = conn or self.conn
        return WebDavFileSource(
            conn, client=build_client(conn, transport=self.transport)
        )

    def test_lists_root_and_nested_folders(self):
        source = self._source()
        root = {e.name: e for e in source.list_dir(source.ROOT_ID)}
        self.assertEqual(set(root), {"Documents", "root.md"})
        self.assertTrue(root["Documents"].is_dir)
        self.assertEqual(root["root.md"].size, 6)
        self.assertEqual(root["Documents"].id, "/Documents")

        nested = list(source.list_dir("/Documents"))
        self.assertEqual([e.id for e in nested], ["/Documents/notes.txt"])
        self.assertFalse(nested[0].is_dir)
        self.assertIsNotNone(nested[0].modified_at)

    def test_streams_file_bytes(self):
        source = self._source()
        entry = next(iter(source.list_dir("/Documents")))
        with source.open(entry) as stream:
            self.assertEqual(stream.read(), b"hello import")

    def test_test_connection_closes_its_client(self):
        clients = []

        def factory(conn, **kw):
            client = build_client(conn, transport=self.transport, **kw)
            clients.append(client)
            return client

        with patch("workspace.imports.providers.webdav.build_client", factory):
            WebDavProvider().test_connection(self.conn)
        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].is_closed)

    def test_probe_reports_quota(self):
        with patch(
            "workspace.imports.providers.webdav.build_client",
            lambda conn, **kw: build_client(conn, transport=self.transport, **kw),
        ):
            capabilities = WebDavProvider().test_connection(self.conn)
        self.assertEqual(capabilities["kinds"], ["files"])
        self.assertEqual(
            capabilities["quota_used"], len(b"hello import") + len(b"# root")
        )
        self.assertGreater(capabilities["quota_available"], 0)

    def test_wrong_password_is_authentication_failed(self):
        self.conn.set_secret("nope")
        with self.assertRaises(AuthenticationFailed):
            list(self._source().list_dir("/"))

    def test_missing_folder_is_remote_not_found(self):
        with self.assertRaises(RemoteNotFound):
            list(self._source().list_dir("/nowhere"))

    def test_open_missing_file_is_remote_not_found(self):
        entry = RemoteEntry(id="/Documents/gone.txt", name="gone.txt", is_dir=False)
        with self.assertRaises(RemoteNotFound):
            with self._source().open(entry):
                pass
