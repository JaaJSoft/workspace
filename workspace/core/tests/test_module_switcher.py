"""The navbar module switcher: shown inside a module, absent on the home page."""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class ModuleSwitcherNavbarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="switcher", password="pass123")
        self.client.login(username="switcher", password="pass123")

    def test_module_page_shows_the_switcher_instead_of_the_logo(self):
        resp = self.client.get("/files")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="module-switcher"')
        self.assertContains(resp, 'id="dashboard-modules-grid"')
        self.assertContains(resp, "/dashboard/modules?current=files")
        self.assertNotContains(resp, 'id="navbar-brand"')

    def test_home_page_keeps_the_logo(self):
        resp = self.client.get("/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="navbar-brand"')
        self.assertNotContains(resp, 'id="module-switcher"')
