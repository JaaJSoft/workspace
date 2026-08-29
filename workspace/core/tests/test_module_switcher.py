"""The navbar module switcher: shown inside a module, absent on the home page."""

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

    def test_home_page_keeps_the_logo(self):
        resp = self.client.get("/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="navbar-brand"')
        self.assertNotContains(resp, 'id="module-switcher"')

    @override_settings(PREVIEW_VISIBILITY="all")
    def test_module_dropped_from_the_dashboard_still_gets_a_current_tile(self):
        resp = self.client.get("/imports")

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # Scope the assertion to the switcher grid: the user-menu list of
        # modules kept off the dashboard also renders an aria-current entry
        # for /imports, which would mask a missing grid tile if counted
        # page-wide.
        start = content.index('id="module-switcher-grid"')
        end = content.index("</section>", start)
        grid_html = content[start:end]
        self.assertIn("imports", grid_html)
        self.assertEqual(grid_html.count('aria-current="page"'), 1)
