from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from workspace.users.context_processors import user_preferences
from workspace.users.services.settings import set_setting

User = get_user_model()


class UserPreferencesContextProcessorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='alice', password='pass')

    def tearDown(self):
        cache.clear()

    def _request(self, user):
        req = self.factory.get('/')
        req.user = user
        return req

    def test_anonymous_returns_empty_dict(self):
        self.assertEqual(user_preferences(self._request(AnonymousUser())), {})

    def test_missing_user_attr_returns_empty_dict(self):
        req = self.factory.get('/')
        self.assertEqual(user_preferences(req), {})

    def test_authenticated_returns_stored_preferences(self):
        set_setting(self.user, 'core', 'theme', 'dark')
        set_setting(self.user, 'core', 'timezone', 'Europe/Paris')

        ctx = user_preferences(self._request(self.user))
        self.assertEqual(ctx, {'user_theme': 'dark', 'user_timezone': 'Europe/Paris'})

    def test_authenticated_defaults_when_nothing_stored(self):
        ctx = user_preferences(self._request(self.user))
        self.assertEqual(ctx, {'user_theme': 'light', 'user_timezone': ''})
