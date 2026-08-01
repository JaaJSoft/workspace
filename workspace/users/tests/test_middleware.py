from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone as dj_timezone

from workspace.users.middleware import (
    AjaxLoginRedirectMiddleware,
    PresenceMiddleware,
    TimezoneMiddleware,
)
from workspace.users.services.settings import set_setting

User = get_user_model()


# ── AjaxLoginRedirectMiddleware ─────────────────────────────────


class AjaxLoginRedirectMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get_middleware(self, response):
        return AjaxLoginRedirectMiddleware(lambda request: response)

    @override_settings(LOGIN_URL="/accounts/login/")
    def test_ajax_redirect_to_login_returns_401(self):
        response = HttpResponseRedirect("/accounts/login/?next=/dashboard")
        middleware = self._get_middleware(response)
        request = self.factory.get("/dashboard", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        result = middleware(request)
        self.assertEqual(result.status_code, 401)

    @override_settings(LOGIN_URL="/accounts/login/")
    def test_alpine_request_redirect_to_login_returns_401(self):
        response = HttpResponseRedirect("/accounts/login/?next=/dashboard")
        middleware = self._get_middleware(response)
        request = self.factory.get("/dashboard", HTTP_X_ALPINE_REQUEST="true")
        result = middleware(request)
        self.assertEqual(result.status_code, 401)

    @override_settings(LOGIN_URL="/accounts/login/")
    def test_non_ajax_redirect_passes_through(self):
        response = HttpResponseRedirect("/accounts/login/?next=/dashboard")
        middleware = self._get_middleware(response)
        request = self.factory.get("/dashboard")
        result = middleware(request)
        self.assertEqual(result.status_code, 302)

    @override_settings(LOGIN_URL="/accounts/login/")
    def test_non_login_redirect_passes_through(self):
        response = HttpResponseRedirect("/somewhere-else/")
        middleware = self._get_middleware(response)
        request = self.factory.get("/page", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        result = middleware(request)
        self.assertEqual(result.status_code, 302)

    @override_settings(LOGIN_URL="/accounts/login/")
    def test_non_redirect_passes_through(self):
        response = HttpResponse("OK", status=200)
        middleware = self._get_middleware(response)
        request = self.factory.get("/page", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        result = middleware(request)
        self.assertEqual(result.status_code, 200)


# ── PresenceMiddleware ──────────────────────────────────────────


class PresenceMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="alice", password="pass")

    def _get_middleware(self):
        return PresenceMiddleware(lambda request: HttpResponse("OK"))

    @patch("workspace.users.middleware.presence_service")
    def test_touches_authenticated_user(self, mock_ps):
        middleware = self._get_middleware()
        request = self.factory.get("/page")
        request.user = self.user
        middleware(request)
        mock_ps.touch.assert_called_once_with(self.user.id)

    @patch("workspace.users.middleware.presence_service")
    def test_skips_anonymous_user(self, mock_ps):
        middleware = self._get_middleware()
        request = self.factory.get("/page")
        request.user = AnonymousUser()
        middleware(request)
        mock_ps.touch.assert_not_called()

    @patch("workspace.users.middleware.presence_service")
    def test_skips_sse_stream(self, mock_ps):
        middleware = self._get_middleware()
        request = self.factory.get("/page")
        request.user = self.user
        request._is_sse_stream = True
        middleware(request)
        mock_ps.touch.assert_not_called()

    @patch("workspace.users.middleware.presence_service")
    def test_skips_when_no_user_attr(self, mock_ps):
        middleware = self._get_middleware()
        request = self.factory.get("/page")
        # request has no 'user' attribute (raw request)
        if hasattr(request, "user"):
            delattr(request, "user")
        middleware(request)
        mock_ps.touch.assert_not_called()


# ── TimezoneMiddleware ──────────────────────────────────────────


class TimezoneMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="tzuser", password="pass")

    def tearDown(self):
        cache.clear()
        dj_timezone.deactivate()

    def _run(self, request):
        seen = {}

        def view(_request):
            seen["tz"] = dj_timezone.get_current_timezone_name()
            return HttpResponse("ok")

        TimezoneMiddleware(view)(request)
        return seen["tz"]

    def test_activates_stored_timezone(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        request = self.factory.get("/")
        request.user = self.user
        self.assertEqual(self._run(request), "Europe/Paris")

    def test_anonymous_request_stays_utc(self):
        request = self.factory.get("/")
        request.user = AnonymousUser()
        self.assertEqual(self._run(request), "UTC")

    def test_invalid_stored_timezone_falls_back_to_utc(self):
        set_setting(self.user, "core", "timezone", "Mars/Olympus")
        request = self.factory.get("/")
        request.user = self.user
        self.assertEqual(self._run(request), "UTC")

    def test_deactivates_after_response(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        request = self.factory.get("/")
        request.user = self.user
        self._run(request)
        self.assertEqual(dj_timezone.get_current_timezone_name(), "UTC")

    def test_registered_in_middleware_stack(self):
        from django.conf import settings as dj_settings

        self.assertIn(
            "workspace.users.middleware.TimezoneMiddleware", dj_settings.MIDDLEWARE
        )
