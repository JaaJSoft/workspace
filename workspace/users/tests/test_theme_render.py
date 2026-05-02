"""Anti-regression for the "F5 reverts my setting" bug class.

The cache invalidation chain is covered piece-by-piece in
``test_settings.py::test_set_setting_invalidates_cache`` and
``test_views.py::test_put_invalidates_cache_so_get_setting_sees_fresh_value``.
What this file adds is the last link: a fresh page render after the
write actually emits ``data-theme="dark"`` in the rendered HTML.

We use Django's test client (not Playwright) on purpose:

* The flow we need to assert is template + context-processor wiring,
  not browser behavior. Django's test client renders the template
  through the same middleware stack the live server uses.
* A previous Playwright version of this test was flaky on Windows
  under ``StaticLiveServerTestCase`` with the in-memory SQLite test
  DB: load-time XHRs racing with the navigation request occasionally
  triggered an empty-session response that wiped the sessionid
  cookie via ``Set-Cookie: sessionid=""; Max-Age=0``, deauthenticating
  the next request. That race is purely a test-environment artifact
  (file-based PG + Redis sessions in production) and shouldn't gate
  our coverage of cache invalidation.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from workspace.users.services.settings import get_setting, set_setting

User = get_user_model()


class ThemeRenderAfterSetTests(TestCase):
    """A page rendered after ``set_setting`` carries the new theme."""

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass12345')
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_theme_set_then_render_reflects_new_value(self):
        # Warm the cache to 'light' so we exercise the cache path -
        # without warming, ``get_setting`` would always read fresh and
        # miss the bug class.
        self.assertEqual(
            get_setting(self.user, 'core', 'theme', default='light'),
            'light',
        )

        # Production write path goes through ``set_setting`` (also via the
        # PUT endpoint - test_views covers that side). The point here is
        # that the very next render sees the new value.
        set_setting(self.user, 'core', 'theme', 'dark')

        resp = self.client.get('/users/settings')
        self.assertEqual(resp.status_code, 200)
        # base.html: ``<html lang="en"{% if user_theme %} data-theme="{{ user_theme }}"{% endif %}>``
        self.assertIn(b'data-theme="dark"', resp.content)
        self.assertNotIn(b'data-theme="light"', resp.content)

    def test_theme_unset_renders_light_default(self):
        # No UserSetting row -> ``user_preferences`` falls back to 'light'
        # via the ``or 'light'`` guard in the context processor.
        resp = self.client.get('/users/settings')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'data-theme="light"', resp.content)
