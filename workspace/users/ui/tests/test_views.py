from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from workspace.core.module_registry import ModuleInfo
from workspace.users.services.settings import set_setting

User = get_user_model()


def _mod(slug, preview=False):
    return ModuleInfo(
        name=slug.title(),
        slug=slug,
        description="",
        icon="i",
        color="c",
        url=f"/{slug}",
        active=True,
        preview=preview,
    )


class SettingsViewDashboardAppsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="prefuser", email="pref@test.com", password="pass123"
        )
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    @patch("workspace.users.ui.views.visible_modules")
    def test_dashboard_apps_marks_hidden(self, mock_visible):
        mock_visible.return_value = [_mod("chat"), _mod("mail"), _mod("dashboard")]
        set_setting(self.user, "dashboard", "hidden_modules", ["mail"])

        response = self.client.get(reverse("users_ui:settings"))

        apps = {a["slug"]: a for a in response.context["dashboard_apps"]}
        self.assertNotIn("dashboard", apps)  # dashboard tile excluded
        self.assertFalse(apps["chat"]["hidden"])
        self.assertTrue(apps["mail"]["hidden"])

    @patch("workspace.users.ui.views.visible_modules")
    def test_dashboard_apps_default_all_visible(self, mock_visible):
        mock_visible.return_value = [_mod("chat"), _mod("files")]

        response = self.client.get(reverse("users_ui:settings"))

        apps = {a["slug"]: a for a in response.context["dashboard_apps"]}
        self.assertFalse(apps["chat"]["hidden"])
        self.assertFalse(apps["files"]["hidden"])
