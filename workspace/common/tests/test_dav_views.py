import base64

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from workspace.common.dav.auth import clear_auth_cache
from workspace.common.dav.views import DavView, check_preconditions, depth, etag_matches
from workspace.common.dav.xml import DavError

User = get_user_model()


class _Resource(DavView):
    def propfind(self, request):
        return HttpResponse(request.user.username, status=207)

    def put(self, request):
        raise DavError(412)


def _auth(username, password):
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


class DavViewTests(TestCase):
    def setUp(self):
        clear_auth_cache()
        self.factory = RequestFactory()
        User.objects.create_user(username="alice", password="pw-alice-1")

    def tearDown(self):
        clear_auth_cache()

    def _call(self, method, **headers):
        request = self.factory.generic(method, "/dav/x", headers=headers)
        return _Resource.as_view()(request)

    def test_anonymous_request_gets_a_basic_challenge(self):
        response = self._call("PROPFIND")
        self.assertEqual(response.status_code, 401)
        self.assertIn('Basic realm="Workspace"', response["WWW-Authenticate"])

    def test_wrong_password_gets_a_challenge(self):
        response = self._call("PROPFIND", Authorization=_auth("alice", "nope"))
        self.assertEqual(response.status_code, 401)

    def test_authenticated_dav_method_reaches_the_handler(self):
        response = self._call("PROPFIND", Authorization=_auth("alice", "pw-alice-1"))
        self.assertEqual(response.status_code, 207)
        self.assertEqual(response.content, b"alice")

    def test_dav_error_becomes_its_status(self):
        response = self._call("PUT", Authorization=_auth("alice", "pw-alice-1"))
        self.assertEqual(response.status_code, 412)

    def test_unsupported_method_is_405_with_allow(self):
        response = self._call("MKCOL", Authorization=_auth("alice", "pw-alice-1"))
        self.assertEqual(response.status_code, 405)
        self.assertIn("PROPFIND", response["Allow"])

    def test_options_advertises_compliance(self):
        response = self._call("OPTIONS", Authorization=_auth("alice", "pw-alice-1"))
        self.assertEqual(response["DAV"], "1, 3")
        self.assertIn("PUT", response["Allow"])


class ConditionalTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_etag_matches(self):
        self.assertTrue(etag_matches('"a", "b"', '"b"'))
        self.assertTrue(etag_matches('W/"b"', '"b"'))
        self.assertTrue(etag_matches("*", '"b"'))
        self.assertFalse(etag_matches("*", None))
        self.assertFalse(etag_matches('"a"', '"b"'))

    def test_if_match_on_a_changed_resource_fails(self):
        request = self.factory.put("/x", headers={"If-Match": '"old"'})
        with self.assertRaises(DavError) as ctx:
            check_preconditions(request, '"new"')
        self.assertEqual(ctx.exception.status, 412)

    def test_if_none_match_star_refuses_an_existing_resource(self):
        request = self.factory.put("/x", headers={"If-None-Match": "*"})
        with self.assertRaises(DavError):
            check_preconditions(request, '"etag"')
        check_preconditions(request, None)

    def test_depth(self):
        request = self.factory.generic("PROPFIND", "/x", headers={"Depth": "1"})
        self.assertEqual(depth(request), "1")
        self.assertEqual(depth(self.factory.generic("PROPFIND", "/x")), "infinity")
        with self.assertRaises(DavError):
            depth(self.factory.generic("PROPFIND", "/x", headers={"Depth": "2"}))
