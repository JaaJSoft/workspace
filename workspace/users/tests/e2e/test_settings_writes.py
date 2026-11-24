"""E2E: the settings page stores what the user picks, and says so when the
server refuses it.

These pickers are inline scripts of the settings templates, out of reach of
the Node tests: only a real page runs them.
"""

from __future__ import annotations

import json

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.users.services.settings import get_setting, set_setting


class SettingsWritesTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        set_setting(self.user, "core", "timezone", "UTC")
        self.login_as(self.user)

    def tearDown(self):
        super().tearDown()
        cache.clear()

    def _open(self, tab):
        self.page.goto(f"{self.live_server_url}/users/settings#{tab}")
        self.page.wait_for_load_state("networkidle")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _refuse(self, path, body=None):
        self.page.route(
            f"**{path}",
            lambda route: route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps(body or {"value": ["Refused."]}),
            ),
        )

    def _write(self, method, path):
        return self.page.expect_response(
            lambda r: r.request.method == method and r.url.endswith(path)
        )

    def test_picking_a_light_theme_stores_it_and_makes_it_current(self):
        self._open("appearance")
        with self._write("PATCH", "/api/v1/settings/core"):
            self.page.get_by_role("button", name="Cupcake").click()

        expect(self.page.locator("html")).to_have_attribute("data-theme", "cupcake")
        self.assertEqual(get_setting(self.user, "core", "light_theme"), "cupcake")
        self.assertEqual(get_setting(self.user, "core", "theme"), "cupcake")

    def test_picking_a_timezone_stores_it(self):
        self._open("region")
        with self._write("PUT", "/api/v1/settings/core/timezone"):
            self.page.get_by_label("Timezone").select_option("Asia/Tokyo")

        expect(self.page.locator("html")).to_have_attribute(
            "data-timezone", "Asia/Tokyo"
        )
        self.assertEqual(get_setting(self.user, "core", "timezone"), "Asia/Tokyo")

    def test_a_refused_timezone_is_put_back_and_reported(self):
        self._open("region")
        self._refuse("/api/v1/settings/core/timezone")
        with self._write("PUT", "/api/v1/settings/core/timezone"):
            self.page.get_by_label("Timezone").select_option("Asia/Tokyo")

        expect(self.page.locator("html")).to_have_attribute("data-timezone", "UTC")
        expect(self.page.get_by_text("Failed to save timezone")).to_be_visible()
        self.assertEqual(get_setting(self.user, "core", "timezone"), "UTC")

    def test_picking_a_time_format_stores_it(self):
        self._open("region")
        with self._write("PUT", "/api/v1/settings/core/time_format"):
            self.page.get_by_label("Time format").select_option("12h")

        expect(self.page.locator("html")).to_have_attribute("data-time-format", "12h")
        self.assertEqual(get_setting(self.user, "core", "time_format"), "12h")

    def test_a_refused_time_format_is_put_back_and_reported(self):
        self._open("region")
        self._refuse("/api/v1/settings/core/time_format")
        with self._write("PUT", "/api/v1/settings/core/time_format"):
            self.page.get_by_label("Time format").select_option("12h")

        expect(self.page.locator("html")).to_have_attribute("data-time-format", "24h")
        expect(self.page.get_by_text("Failed to save time format")).to_be_visible()

    def test_a_role_is_saved_on_blur(self):
        self._open("profile")
        field = self.page.get_by_placeholder("ex: Lead Backend, Designer...")
        field.fill("Designer")
        with self._write("PUT", "/api/v1/settings/profile/role"):
            field.blur()

        expect(self.page.get_by_text("Role saved.")).to_be_visible()
        self.assertEqual(get_setting(self.user, "profile", "role"), "Designer")

    def test_a_refused_role_shows_the_server_s_reason(self):
        self._open("profile")
        self._refuse("/api/v1/settings/profile/role", {"value": ["Not a role."]})
        field = self.page.get_by_placeholder("ex: Lead Backend, Designer...")
        field.fill("Designer")
        with self._write("PUT", "/api/v1/settings/profile/role"):
            field.blur()

        expect(self.page.get_by_text("Not a role.")).to_be_visible()

    def test_a_banner_preset_is_saved(self):
        self._open("profile")
        with self._write("PUT", "/api/v1/settings/profile/banner_palette"):
            self.page.locator('button[title="Ocean"]').click()

        expect(self.page.get_by_text("Banner palette saved.")).to_be_visible()
        self.assertEqual(get_setting(self.user, "profile", "banner_palette"), "ocean")

    def test_a_refused_banner_preset_is_reported(self):
        self._open("profile")
        self._refuse("/api/v1/settings/profile/banner_palette")
        with self._write("PUT", "/api/v1/settings/profile/banner_palette"):
            self.page.locator('button[title="Ocean"]').click()

        expect(self.page.get_by_text("Failed to save palette.")).to_be_visible()
