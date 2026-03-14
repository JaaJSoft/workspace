from django.test import TestCase, RequestFactory
from django_ratelimit.exceptions import Ratelimited

from workspace.common.views_errors import handler429


class Handler429Test(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_html_request_returns_429(self):
        request = self.factory.get('/', HTTP_ACCEPT='text/html')
        response = handler429(request, Ratelimited())
        self.assertEqual(response.status_code, 429)
        self.assertIn(b'Too many requests', response.content)

    def test_json_request_returns_json(self):
        request = self.factory.get(
            '/', HTTP_ACCEPT='application/json', content_type='application/json'
        )
        response = handler429(request, Ratelimited())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Content-Type'], 'application/json')
