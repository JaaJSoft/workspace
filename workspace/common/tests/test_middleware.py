from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import path

from workspace.common.middleware import HtmlCacheControlMiddleware

factory = RequestFactory()


def _run(response):
    middleware = HtmlCacheControlMiddleware(lambda request: response)
    return middleware(factory.get("/"))


class HtmlCacheControlMiddlewareTests(SimpleTestCase):
    def test_html_response_gets_private_no_cache(self):
        response = _run(HttpResponse("<html></html>"))
        self.assertEqual(response["Cache-Control"], "private, no-cache")

    def test_existing_cache_control_is_preserved(self):
        original = HttpResponse("<html></html>")
        original["Cache-Control"] = "private, max-age=604800, immutable"
        response = _run(original)
        self.assertEqual(
            response["Cache-Control"], "private, max-age=604800, immutable"
        )

    def test_non_html_response_untouched(self):
        response = _run(JsonResponse({"ok": True}))
        self.assertFalse(response.has_header("Cache-Control"))


def _html_page(request):
    return HttpResponse("<html><body>stable body</body></html>")


urlpatterns = [path("test-html-page", _html_page)]


@override_settings(ROOT_URLCONF="workspace.common.tests.test_middleware")
class FullStackCacheControlTests(TestCase):
    """The policy and the ETag/304 path must coexist through the real stack."""

    def test_html_page_carries_policy_and_etag(self):
        response = self.client.get("/test-html-page")
        self.assertEqual(response["Cache-Control"], "private, no-cache")
        self.assertTrue(response.has_header("ETag"))

    def test_conditional_get_returns_304(self):
        etag = self.client.get("/test-html-page")["ETag"]
        response = self.client.get("/test-html-page", HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(response.status_code, 304)
        self.assertEqual(response["Cache-Control"], "private, no-cache")


class RealPagesCacheControlTests(TestCase):
    def test_authenticated_dashboard_carries_policy(self):
        user = get_user_model().objects.create_user("alice", password="pw")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-cache")

    def test_login_page_keeps_its_never_cache_policy(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
