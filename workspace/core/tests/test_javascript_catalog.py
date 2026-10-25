from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from workspace.users.services.settings import set_setting

User = get_user_model()


class JavaScriptCatalogTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_serves_the_catalog_of_the_language_in_the_url(self):
        response = self.client.get("/jsi18n/fr.js")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/javascript"))
        self.assertEqual(response["Content-Language"], "fr")
        self.assertContains(response, '"Today": "Aujourd\\u2019hui"')

    def test_language_comes_from_the_url_not_the_user(self):
        # A browser-cached response must not depend on who asked for it.
        user = User.objects.create_user(username="catalog", password="pass")
        set_setting(user, "core", "language", "fr")
        self.client.force_login(user)
        response = self.client.get("/jsi18n/en.js")
        self.assertEqual(response["Content-Language"], "en")
        self.assertNotContains(response, "Aujourd")

    def test_unknown_language_is_not_found(self):
        self.assertEqual(self.client.get("/jsi18n/xx.js").status_code, 404)

    def test_django_own_catalogs_stay_out(self):
        # The admin's djangojs catalog translates "Today" in its own words
        # (with a straight apostrophe) and "Now", which no workspace catalog
        # carries.
        response = self.client.get("/jsi18n/fr.js")
        self.assertNotContains(response, "Aujourd'hui")
        self.assertNotContains(response, '"Now"')

    @override_settings(DEBUG=False)
    def test_browser_may_cache_it(self):
        response = self.client.get("/jsi18n/fr.js")
        self.assertIn("max-age=86400", response["Cache-Control"])
        self.assertIn("public", response["Cache-Control"])

    @override_settings(DEBUG=True)
    def test_revalidated_in_debug(self):
        response = self.client.get("/jsi18n/fr.js")
        self.assertEqual(response["Cache-Control"], "no-cache")
