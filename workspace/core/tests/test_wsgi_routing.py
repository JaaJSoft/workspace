import io
from unittest import mock

from django.test import SimpleTestCase

from workspace import wsgi

DISCOVERY = b'<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
QUOTA = b'<d:propfind xmlns:d="DAV:"><d:prop><d:quota-available-bytes/></d:prop></d:propfind>'


class RootPropfindRoutingTests(SimpleTestCase):
    """PROPFIND on "/" serves two kinds of clients that never ask the same thing."""

    def _route(self, body, method="PROPFIND"):
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": "/",
            "SCRIPT_NAME": "",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
        }
        django_app = mock.Mock(return_value=[b"django"])
        dav_app = mock.Mock(return_value=[b"dav"])
        with (
            mock.patch.object(wsgi, "_django_app", django_app),
            mock.patch.object(wsgi, "_get_webdav_app", return_value=dav_app),
        ):
            result = wsgi.application(environ, mock.Mock())
        return result, environ

    def test_calendar_discovery_reaches_caldav(self):
        result, environ = self._route(DISCOVERY)
        self.assertEqual(result, [b"django"])
        self.assertEqual(environ["PATH_INFO"], "/caldav/")
        self.assertEqual(environ["wsgi.input"].read(), DISCOVERY)

    def test_file_client_still_reaches_webdav(self):
        result, environ = self._route(QUOTA)
        self.assertEqual(result, [b"dav"])
        self.assertEqual(environ["wsgi.input"].read(), QUOTA)

    def test_empty_propfind_reaches_webdav(self):
        result, _ = self._route(b"")
        self.assertEqual(result, [b"dav"])
