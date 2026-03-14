from django.http import HttpResponse
from django.test import TestCase, RequestFactory
from django_ratelimit.exceptions import Ratelimited

from workspace.common.middleware_ratelimit import RatelimitMiddleware


class RatelimitMiddlewareTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get_middleware(self):
        def dummy_get_response(request):
            return HttpResponse('OK')
        return RatelimitMiddleware(dummy_get_response)

    def test_normal_request_passes_through(self):
        mw = self._get_middleware()
        request = self.factory.get('/')
        response = mw(request)
        self.assertEqual(response.status_code, 200)

    def test_ratelimited_api_returns_json_429(self):
        mw = self._get_middleware()
        request = self.factory.get('/api/v1/something', content_type='application/json')
        response = mw.process_exception(request, Ratelimited())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_ratelimited_html_returns_html_429(self):
        mw = self._get_middleware()
        request = self.factory.get('/calendar/polls/shared/abc', HTTP_ACCEPT='text/html')
        response = mw.process_exception(request, Ratelimited())
        self.assertEqual(response.status_code, 429)
        self.assertIn(b'Too many requests', response.content)

    def test_retry_after_header_present(self):
        mw = self._get_middleware()
        request = self.factory.get('/api/v1/something', content_type='application/json')
        response = mw.process_exception(request, Ratelimited())
        self.assertIn('Retry-After', response)
        self.assertEqual(response['Retry-After'], '60')

    def test_non_ratelimit_exception_ignored(self):
        mw = self._get_middleware()
        request = self.factory.get('/')
        result = mw.process_exception(request, ValueError('test'))
        self.assertIsNone(result)
