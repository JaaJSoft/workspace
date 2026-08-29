"""The navbar module switcher: the module tile inside a module, the logo elsewhere."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()


class ModuleSwitcherNavbarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="switcher", password="pass123")
        self.client.login(username="switcher", password="pass123")

    def test_module_page_shows_the_switcher_instead_of_the_logo(self):
        resp = self.client.get("/files")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="module-switcher"')
        self.assertNotContains(resp, 'id="navbar-brand"')

    def test_grid_is_rendered_with_the_page_and_marks_the_current_module(self):
        resp = self.client.get("/files")

        self.assertContains(resp, 'id="module-switcher-grid"')
        self.assertContains(resp, 'aria-current="page"', count=1)
        self.assertNotContains(resp, "/dashboard/modules")

    def test_home_page_opens_the_switcher_from_the_logo(self):
        resp = self.client.get("/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="module-switcher"')
        self.assertContains(resp, 'id="navbar-brand"')
        grid_html = self._grid_html(resp)
        self.assertEqual(grid_html.count('aria-current="page"'), 1)
        self.assertIn('href="/"', grid_html[: grid_html.index('aria-current="page"')])

    def test_pages_outside_a_module_keep_the_logo_as_trigger(self):
        resp = self.client.get("/users/settings")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="module-switcher"')
        self.assertContains(resp, 'id="navbar-brand"')
        self.assertNotIn('aria-current="page"', self._grid_html(resp))

    def test_anonymous_pages_keep_the_plain_logo(self):
        self.client.logout()
        resp = self.client.get("/login")

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="module-switcher"')

    def _grid_html(self, resp):
        content = resp.content.decode()
        start = content.index('id="module-switcher-grid"')
        return content[start : content.index("</section>", start)]

    @override_settings(PREVIEW_VISIBILITY="all")
    def test_module_dropped_from_the_dashboard_still_gets_a_current_tile(self):
        resp = self.client.get("/imports")

        self.assertEqual(resp.status_code, 200)
        # Scope the assertion to the switcher grid: the user-menu list of
        # modules kept off the dashboard also renders an aria-current entry
        # for /imports, which would mask a missing grid tile if counted
        # page-wide.
        grid_html = self._grid_html(resp)
        self.assertIn("imports", grid_html)
        self.assertEqual(grid_html.count('aria-current="page"'), 1)
