"""The module switcher: the sidebar header title inside a module opens the apps grid."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.users.services.settings import set_setting

User = get_user_model()


class ModuleSwitcherTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="switcher", password="pass123")
        self.client.login(username="switcher", password="pass123")

    def tearDown(self):
        cache.clear()

    def test_module_page_renders_the_switcher_in_its_sidebar_header(self):
        resp = self.client.get("/files")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="module-switcher"')
        self.assertContains(resp, 'id="navbar-brand"')
        self.assertContains(resp, "Switch module, currently Files")
        self.assertEqual(self._grid_html(resp).count('aria-current="page"'), 1)

    def test_home_page_has_no_switcher(self):
        resp = self.client.get("/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="navbar-brand"')
        self.assertNotContains(resp, 'id="module-switcher"')

    def test_pages_outside_a_module_have_no_switcher(self):
        resp = self.client.get("/users/settings")

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="module-switcher"')

    def test_module_hidden_from_the_dashboard_still_gets_a_current_tile(self):
        set_setting(self.user, "dashboard", "hidden_modules", ["files"])

        resp = self.client.get("/files")

        self.assertEqual(resp.status_code, 200)
        grid_html = self._grid_html(resp)
        self.assertIn('href="/files"', grid_html)
        self.assertEqual(grid_html.count('aria-current="page"'), 1)

    def _grid_html(self, resp):
        content = resp.content.decode()
        start = content.index('id="module-switcher-grid"')
        return content[start : content.index("</section>", start)]
