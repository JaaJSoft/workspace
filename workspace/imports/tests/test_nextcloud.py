import httpx2
from django.test import SimpleTestCase

from workspace.imports.models import ImportConnection
from workspace.imports.providers.base import ProviderError
from workspace.imports.providers.nextcloud import (
    NextcloudMetadataSource,
    NextcloudProvider,
    _instance_root,
)


def _connection(base_url):
    conn = ImportConnection(
        provider="nextcloud", label="nc", base_url=base_url, username="alice"
    )
    conn.set_secret("pw")
    return conn


class NormalizeBaseUrlTests(SimpleTestCase):
    def setUp(self):
        self.provider = NextcloudProvider()

    def test_instance_url_gets_the_per_user_dav_root(self):
        self.assertEqual(
            self.provider.normalize_base_url("https://cloud.example.org/", "alice"),
            "https://cloud.example.org/remote.php/dav/files/alice",
        )

    def test_instance_under_a_sub_path(self):
        self.assertEqual(
            self.provider.normalize_base_url("https://example.org/nextcloud", "alice"),
            "https://example.org/nextcloud/remote.php/dav/files/alice",
        )

    def test_full_dav_url_is_kept(self):
        url = "https://cloud.example.org/remote.php/dav/files/alice"
        self.assertEqual(self.provider.normalize_base_url(url + "/", "alice"), url)

    def test_dav_url_follows_a_username_change(self):
        self.assertEqual(
            self.provider.normalize_base_url(
                "https://cloud.example.org/remote.php/dav/files/alice/Photos", "bob"
            ),
            "https://cloud.example.org/remote.php/dav/files/bob/Photos",
        )

    def test_legacy_webdav_url_is_kept(self):
        url = "https://cloud.example.org/remote.php/webdav"
        self.assertEqual(self.provider.normalize_base_url(url, "alice"), url)


class InstanceRootTests(SimpleTestCase):
    def test_strips_the_dav_part(self):
        self.assertEqual(
            _instance_root("https://cloud.example.org/remote.php/dav/files/alice"),
            "https://cloud.example.org",
        )
        self.assertEqual(
            _instance_root("https://example.org/nc/remote.php/webdav/"),
            "https://example.org/nc",
        )


class DiscoveryTests(SimpleTestCase):
    def _discover(self, handler):
        provider = NextcloudProvider()
        conn = _connection("https://cloud.example.org/remote.php/dav/files/alice")
        from unittest.mock import patch

        from workspace.imports.providers import webdav

        def fake_build_client(connection, *, base_url=None, **kwargs):
            return httpx2.Client(
                base_url=base_url or connection.base_url,
                transport=httpx2.MockTransport(handler),
            )

        with (
            patch.object(webdav, "build_client", fake_build_client),
            patch(
                "workspace.imports.providers.nextcloud.build_client", fake_build_client
            ),
        ):
            return provider._discover(conn)

    def test_reads_version_and_apps_from_ocs(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["ocs"] = request.headers.get("OCS-APIRequest")
            return httpx2.Response(
                200,
                json={
                    "ocs": {
                        "data": {
                            "version": {"major": 31, "string": "31.0.2"},
                            "capabilities": {"files": {}, "deck": {}, "notes": {}},
                        }
                    }
                },
            )

        result = self._discover(handler)
        self.assertEqual(
            result, {"server_version": "31.0.2", "apps": ["deck", "files", "notes"]}
        )
        self.assertTrue(
            seen["url"].startswith(
                "https://cloud.example.org/ocs/v1.php/cloud/capabilities"
            )
        )
        self.assertEqual(seen["ocs"], "true")

    def test_non_200_is_silently_empty(self):
        self.assertEqual(self._discover(lambda r: httpx2.Response(404)), {})

    def test_unexpected_payload_is_silently_empty(self):
        self.assertEqual(
            self._discover(lambda r: httpx2.Response(200, json={"x": 1})), {}
        )

    def test_transport_error_is_silently_empty(self):
        def handler(request):
            raise httpx2.ConnectError("down")

        self.assertEqual(self._discover(handler), {})

    def test_logged_failure_text_is_scrubbed(self):
        from unittest.mock import patch

        def handler(request):
            raise httpx2.ConnectError("line one\r\nFORGED line")

        with patch(
            "workspace.imports.providers.nextcloud.scrub", side_effect=lambda v: v
        ) as scrub:
            self._discover(handler)
        scrubbed = [str(call.args[0]) for call in scrub.call_args_list]
        self.assertTrue(any("Could not reach" in s for s in scrubbed), scrubbed)


class TestConnectionTests(SimpleTestCase):
    def test_merges_webdav_probe_and_ocs_discovery(self):
        from unittest.mock import patch

        provider = NextcloudProvider()
        conn = _connection("https://cloud.example.org/remote.php/dav/files/alice")
        with (
            patch(
                "workspace.imports.providers.webdav.WebDavFileSource.probe",
                return_value={"quota_used": 1, "quota_available": 2},
            ),
            patch.object(
                provider, "_discover", return_value={"server_version": "31.0.2"}
            ),
        ):
            capabilities = provider.test_connection(conn)
        self.assertEqual(
            capabilities,
            {
                "kinds": ["files"],
                "quota_used": 1,
                "quota_available": 2,
                "server_version": "31.0.2",
            },
        )


def _multistatus(*hrefs):
    body = "".join(
        f"<d:response><d:href>{href}</d:href>"
        "<d:propstat><d:status>HTTP/1.1 200 OK</d:status>"
        "<d:prop><d:getetag>&quot;e&quot;</d:getetag></d:prop></d:propstat></d:response>"
        for href in hrefs
    )
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        f"{body}</d:multistatus>"
    )


def _systemtags(*tags):
    body = "".join(
        f"<d:response><d:href>/remote.php/dav/systemtags/{tag_id}</d:href>"
        "<d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop>"
        f"<oc:id>{tag_id}</oc:id><oc:display-name>{name}</oc:display-name>"
        f"<oc:user-visible>{visible}</oc:user-visible>"
        "</d:prop></d:propstat></d:response>"
        for tag_id, name, visible in tags
    )
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        f"{body}</d:multistatus>"
    )


class MetadataSourceTests(SimpleTestCase):
    BASE = "https://cloud.example.org/remote.php/dav/files/alice"

    def _source(self, handler, base_url=None):
        conn = _connection(base_url or self.BASE)
        client = httpx2.Client(
            base_url=_instance_root(conn.base_url),
            transport=httpx2.MockTransport(handler),
        )
        return NextcloudMetadataSource(conn, client=client)

    def test_favorites_are_read_as_entry_ids_relative_to_the_files_root(self):
        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx2.Response(
                207,
                content=_multistatus(
                    "/remote.php/dav/files/alice/Docs/",
                    "/remote.php/dav/files/alice/Docs/re%20port.pdf",
                ),
            )

        with self._source(handler) as source:
            self.assertEqual(list(source.favorites()), ["/Docs", "/Docs/re port.pdf"])
        self.assertEqual(seen["method"], "REPORT")
        self.assertEqual(
            seen["url"], "https://cloud.example.org/remote.php/dav/files/alice/"
        )
        self.assertIn("<oc:favorite>1</oc:favorite>", seen["body"])

    def test_tags_skip_the_ones_the_user_cannot_see(self):
        def handler(request):
            self.assertEqual(request.method, "PROPFIND")
            self.assertEqual(
                str(request.url),
                "https://cloud.example.org/remote.php/dav/systemtags/",
            )
            self.assertEqual(request.headers["Depth"], "1")
            return httpx2.Response(
                207,
                content=_systemtags(
                    ("4", "Invoices", "true"),
                    ("7", "Hidden", "false"),
                    ("9", "Photos", "true"),
                ),
            )

        with self._source(handler) as source:
            self.assertEqual(
                [(t.id, t.name) for t in source.tags()],
                [("4", "Invoices"), ("9", "Photos")],
            )

    def test_a_hidden_tag_is_skipped_however_the_server_spells_the_flag(self):
        def handler(request):
            return httpx2.Response(
                207,
                content=_systemtags(
                    ("1", "Zero", "0"),
                    ("2", "No", "no"),
                    ("3", "False", "False"),
                    ("4", "One", "1"),
                ),
            )

        with self._source(handler) as source:
            self.assertEqual([t.name for t in source.tags()], ["One"])

    def test_a_tag_without_the_visibility_property_is_kept(self):
        def handler(request):
            return httpx2.Response(
                207,
                content=(
                    '<?xml version="1.0"?>'
                    '<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
                    "<d:response><d:href>/remote.php/dav/systemtags/4</d:href>"
                    "<d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop>"
                    "<oc:id>4</oc:id><oc:display-name>Invoices</oc:display-name>"
                    "</d:prop></d:propstat></d:response></d:multistatus>"
                ),
            )

        with self._source(handler) as source:
            self.assertEqual([t.name for t in source.tags()], ["Invoices"])

    def test_tags_without_a_numeric_id_or_a_name_are_skipped(self):
        def handler(request):
            return httpx2.Response(
                207,
                content=_systemtags(("../4", "Evil", "true"), ("5", "", "true")),
            )

        with self._source(handler) as source:
            self.assertEqual(list(source.tags()), [])

    def test_tagged_filters_on_the_tag_id(self):
        seen = {}

        def handler(request):
            seen["body"] = request.content.decode()
            return httpx2.Response(
                207, content=_multistatus("/remote.php/dav/files/alice/a.txt")
            )

        with self._source(handler) as source:
            self.assertEqual(list(source.tagged("4")), ["/a.txt"])
        self.assertIn("<oc:systemtag>4</oc:systemtag>", seen["body"])

    def test_a_tag_id_that_is_not_a_number_never_reaches_the_server(self):
        def handler(request):  # pragma: no cover - must not be called
            raise AssertionError("no request expected")

        with self._source(handler) as source:
            self.assertEqual(list(source.tagged("4</oc:systemtag><evil/>")), [])

    def test_the_files_root_is_read_from_the_connection_sub_path(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx2.Response(207, content=_multistatus())

        source = self._source(
            handler, base_url="https://example.org/nc/remote.php/dav/files/alice"
        )
        with source:
            list(source.favorites())
        self.assertEqual(
            seen["url"], "https://example.org/nc/remote.php/dav/files/alice/"
        )

    def test_a_server_without_the_tags_endpoint_raises(self):
        with self._source(lambda r: httpx2.Response(404)) as source:
            with self.assertRaises(ProviderError):
                list(source.tags())

    def test_a_non_multistatus_answer_raises(self):
        with self._source(lambda r: httpx2.Response(200, content=b"ok")) as source:
            with self.assertRaises(ProviderError):
                list(source.favorites())

    def test_the_provider_offers_the_source(self):
        conn = _connection(self.BASE)
        source = NextcloudProvider().file_metadata_source(conn)
        self.assertIsInstance(source, NextcloudMetadataSource)
        source.close()
