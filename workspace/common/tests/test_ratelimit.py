from django.test import RequestFactory, TestCase

from workspace.common.ratelimit import get_client_key


class GetClientKeyTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_authenticated_user_returns_user_id(self):
        request = self.factory.get('/')
        request.user = type('User', (), {'is_authenticated': True, 'pk': 42})()
        self.assertEqual(get_client_key(None, request), '42')

    def test_anonymous_returns_ip_from_xff(self):
        request = self.factory.get('/', HTTP_X_FORWARDED_FOR='1.2.3.4, 10.0.0.1')
        request.user = type('User', (), {'is_authenticated': False})()
        self.assertEqual(get_client_key(None, request), '1.2.3.4')

    def test_anonymous_returns_remote_addr_fallback(self):
        request = self.factory.get('/', REMOTE_ADDR='9.8.7.6')
        request.user = type('User', (), {'is_authenticated': False})()
        self.assertEqual(get_client_key(None, request), '9.8.7.6')
