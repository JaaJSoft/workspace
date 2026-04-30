"""E2E test: theme toggle persists across a real page reload.

Validates the full chain that backend tests cannot reach end-to-end:
Alpine ``themePickerForm().applyTheme(id)`` →
``PUT /api/v1/settings/core/theme`` →
``set_setting()`` (DB write + ``UserSetting`` cache invalidation) →
``page.reload()`` →
``user_preferences`` context processor calling ``get_setting()`` →
``base.html`` rendering ``<html data-theme="{{ user_theme }}">``.

The backend test suite covers each link in isolation (settings service
cache TTL, the PUT endpoint, the context processor). What only a real
browser can prove is that the cache invalidation actually propagates to
the rendered DOM after F5 — i.e., that we don't regress to the
"F5 reverts my setting" bug class documented in CLAUDE.md.
"""
from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class ThemePersistenceTests(PlaywrightTestCase):
    """Toggling the theme via the UI persists across a full page reload."""

    def test_dark_theme_survives_reload(self):
        user = self.create_user(username="alice")
        self.login_as(user)

        # The settings page reads ``location.hash`` in ``settingsPage().init()``
        # to pick the active tab, so navigating with ``#preferences`` mounts
        # the theme picker without needing to click the sidebar tab.
        self.page.goto(f"{self.live_server_url}/users/settings#preferences")

        # Baseline: no UserSetting row yet → context processor falls back
        # to 'light'. The base template always emits the attribute.
        expect(self.page.locator("html")).to_have_attribute("data-theme", "light")

        # Click the "Dark" tile. Each theme button renders its label via
        # ``<span x-text="t.label">``, so we can match by accessible name.
        # Only the visible (preferences) tab has these buttons.
        dark_button = self.page.get_by_role("button", name="Dark", exact=True)
        expect(dark_button).to_be_visible()

        # Wait specifically for the ``PUT /api/v1/settings/core/theme``
        # response triggered by the click. ``wait_for_load_state(
        # "networkidle")`` does not work here: the SSE long-poll on
        # ``/api/v1/stream`` keeps the page "busy" indefinitely, so
        # networkidle never fires and the test would time out.
        with self.page.expect_response(
            lambda r: (
                r.request.method == "PUT"
                and "/api/v1/settings/core/theme" in r.url
            )
        ) as put_resp:
            dark_button.click()
        assert put_resp.value.ok, (
            f"theme PUT failed: {put_resp.value.status} {put_resp.value.url}"
        )

        # Optimistic UI: ``applyTheme`` mutates ``data-theme`` synchronously
        # before the network request completes — assert it landed.
        expect(self.page.locator("html")).to_have_attribute("data-theme", "dark")

        # F5 — Django re-renders ``<html>`` from scratch using
        # ``user_preferences``, which calls ``get_setting()``. If the
        # 5-min ``UserSetting`` cache was properly invalidated by
        # ``set_setting``, this reads "dark"; otherwise it would still
        # return the cached "light" until the TTL expires.
        self.page.reload()

        expect(self.page.locator("html")).to_have_attribute("data-theme", "dark")
