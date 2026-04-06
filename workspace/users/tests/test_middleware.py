from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, TestCase, override_settings

from workspace.users.middleware import AjaxLoginRedirectMiddleware, PresenceMiddleware

User = get_user_model()


# ── AjaxLoginRedirectMiddleware ─────────────────────────────────

class AjaxLoginRedirectMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get_middleware(self, response):
        return AjaxLoginRedirectMiddleware(lambda request: response)

    @override_settings(LOGIN_URL='/accounts/login/')
    def test_ajax_redirect_to_login_returns_401(self):
        response = HttpResponseRedirect('/accounts/login/?next=/dashboard')
        middleware = self._get_middleware(response)
        request = self.factory.get('/dashboard', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        result = middleware(request)
        self.assertEqual(result.status_code, 401)

    @override_settings(LOGIN_URL='/accounts/login/')
    def test_alpine_request_redirect_to_login_returns_401(self):
        response = HttpResponseRedirect('/accounts/login/?next=/dashboard')
        middleware = self._get_middleware(response)
        request = self.factory.get('/dashboard', HTTP_X_ALPINE_REQUEST='true')
        result = middleware(request)
        self.assertEqual(result.status_code, 401)

    @override_settings(LOGIN_URL='/accounts/login/')
    def test_non_ajax_redirect_passes_through(self):
        response = HttpResponseRedirect('/accounts/login/?next=/dashboard')
        middleware = self._get_middleware(response)
        request = self.factory.get('/dashboard')
        result = middleware(request)
        self.assertEqual(result.status_code, 302)

    @override_settings(LOGIN_URL='/accounts/login/')
    def test_non_login_redirect_passes_through(self):
        response = HttpResponseRedirect('/somewhere-else/')
        middleware = self._get_middleware(response)
        request = self.factory.get('/page', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        result = middleware(request)
        self.assertEqual(result.status_code, 302)

    @override_settings(LOGIN_URL='/accounts/login/')
    def test_non_redirect_passes_through(self):
        response = HttpResponse('OK', status=200)
        middleware = self._get_middleware(response)
        request = self.factory.get('/page', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        result = middleware(request)
        self.assertEqual(result.status_code, 200)


# ── PresenceMiddleware ──────────────────────────────────────────

class PresenceMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='alice', password='pass')

    def _get_middleware(self):
        return PresenceMiddleware(lambda request: HttpResponse('OK'))

    @patch('workspace.users.middleware.presence_service')
    def test_touches_authenticated_user(self, mock_ps):
        middleware = self._get_middleware()
        request = self.factory.get('/page')
        request.user = self.user
        middleware(request)
        mock_ps.touch.assert_called_once_with(self.user.id)

    @patch('workspace.users.middleware.presence_service')
    def test_skips_anonymous_user(self, mock_ps):
        middleware = self._get_middleware()
        request = self.factory.get('/page')
        request.user = AnonymousUser()
        middleware(request)
        mock_ps.touch.assert_not_called()

    @patch('workspace.users.middleware.presence_service')
    def test_skips_sse_stream(self, mock_ps):
        middleware = self._get_middleware()
        request = self.factory.get('/page')
        request.user = self.user
        request._is_sse_stream = True
        middleware(request)
        mock_ps.touch.assert_not_called()

    @patch('workspace.users.middleware.presence_service')
    def test_skips_when_no_user_attr(self, mock_ps):
        middleware = self._get_middleware()
        request = self.factory.get('/page')
        # request has no 'user' attribute (raw request)
        if hasattr(request, 'user'):
            delattr(request, 'user')
        middleware(request)
        mock_ps.touch.assert_not_called()
