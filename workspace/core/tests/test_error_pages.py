from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.test import TestCase, override_settings
from django.urls import path

from workspace.urls import urlpatterns as project_urlpatterns


def _forbidden(request):
    raise PermissionDenied


def _bad_request(request):
    raise SuspiciousOperation


urlpatterns = [
    path("__error-pages/403", _forbidden),
    path("__error-pages/400", _bad_request),
    *project_urlpatterns,
]


@override_settings(ROOT_URLCONF="workspace.core.tests.test_error_pages")
class ErrorPagesTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="lost", password="pw")
        self.client.force_login(user)

    def tearDown(self):
        cache.clear()

    def assertErrorPage(self, resp, status, title, icon, heading, message):
        self.assertEqual(resp.status_code, status)
        body = resp.content.decode()
        self.assertIn(f"<title>{status} - {title}</title>", body)
        self.assertIn(f'data-lucide="{icon}"', body)
        self.assertRegex(body, rf">\s*{status}\s*</div>")
        self.assertIn(heading, body)
        self.assertIn(message, body)
        self.assertIn("data-nav-back", body)

    def test_not_found(self):
        self.assertErrorPage(
            self.client.get("/no-such-page"),
            404,
            "Page Not Found",
            "map-pin-off",
            "Page not found",
            "The page you're looking for doesn't exist or has been moved.",
        )

    def test_forbidden(self):
        self.assertErrorPage(
            self.client.get("/__error-pages/403"),
            403,
            "Forbidden",
            "shield-off",
            "Access denied",
            "You don't have permission to access this page.",
        )

    def test_bad_request(self):
        self.assertErrorPage(
            self.client.get("/__error-pages/400"),
            400,
            "Bad Request",
            "alert-triangle",
            "Bad request",
            "The server couldn't understand your request. Please try again.",
        )
