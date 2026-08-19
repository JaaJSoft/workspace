import httpx2
from django.test import SimpleTestCase

from workspace.imports.models import ImportConnection
from workspace.imports.providers.nextcloud import NextcloudProvider, _instance_root


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
