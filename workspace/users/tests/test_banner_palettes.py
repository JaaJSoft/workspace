from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.users.banner_palettes import resolve_banner_gradient
from workspace.users.services.settings import set_setting

User = get_user_model()


class ResolveBannerGradientTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="testuser", password="testpass")

    def tearDown(self):
        cache.clear()

    def test_returns_none_when_no_setting(self):
        self.assertIsNone(resolve_banner_gradient(self.user))

    def test_resolves_preset_palette(self):
        set_setting(self.user, "profile", "banner_palette", "sunset")
        result = resolve_banner_gradient(self.user)
        self.assertEqual(result, "linear-gradient(135deg, #f97316, #e11d48, #7c3aed)")

    def test_resolves_custom_palette(self):
        custom = {"from": "#111111", "via": "#222222", "to": "#333333"}
        set_setting(self.user, "profile", "banner_palette", custom)
        result = resolve_banner_gradient(self.user)
        self.assertEqual(result, "linear-gradient(135deg, #111111, #222222, #333333)")

    def test_returns_none_for_unknown_preset(self):
        set_setting(self.user, "profile", "banner_palette", "nonexistent")
        self.assertIsNone(resolve_banner_gradient(self.user))

    def test_returns_none_for_invalid_custom_colors(self):
        set_setting(
            self.user,
            "profile",
            "banner_palette",
            {"from": "red", "via": "#222222", "to": "#333333"},
        )
        self.assertIsNone(resolve_banner_gradient(self.user))

    def test_returns_none_for_incomplete_custom(self):
        set_setting(self.user, "profile", "banner_palette", {"from": "#111111"})
        self.assertIsNone(resolve_banner_gradient(self.user))


class ProfileViewContextTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="viewuser", password="testpass")
        self.client.login(username="viewuser", password="testpass")

    def tearDown(self):
        cache.clear()

    def test_profile_view_has_banner_gradient_none_by_default(self):
        resp = self.client.get("/users/profile")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["banner_gradient"])

    def test_profile_view_has_banner_gradient_when_set(self):
        set_setting(self.user, "profile", "banner_palette", "aurora")
        resp = self.client.get("/users/profile")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("linear-gradient", resp.context["banner_gradient"])

    def test_profile_view_includes_bio_and_role(self):
        set_setting(self.user, "profile", "bio", "Hello world")
        set_setting(self.user, "profile", "role", "Dev")
        resp = self.client.get("/users/profile")
        self.assertEqual(resp.context["profile_bio"], "Hello world")
        self.assertEqual(resp.context["profile_role"], "Dev")

    def test_profile_view_bio_and_role_none_by_default(self):
        resp = self.client.get("/users/profile")
        self.assertIsNone(resp.context["profile_bio"])
        self.assertIsNone(resp.context["profile_role"])


class SettingsViewContextTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="settuser", password="testpass")
        self.client.login(username="settuser", password="testpass")

    def test_settings_view_includes_palette_data(self):
        resp = self.client.get("/users/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("banner_palettes", resp.context)
        self.assertEqual(len(resp.context["banner_palettes"]), 8)

    def test_settings_view_includes_bio_role(self):
        resp = self.client.get("/users/settings")
        self.assertEqual(resp.context["profile_bio"], "")
        self.assertEqual(resp.context["profile_role"], "")
