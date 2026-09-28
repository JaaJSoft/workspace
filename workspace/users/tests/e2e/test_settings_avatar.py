"""E2E: changing the profile picture in the settings updates every avatar of
the user already on the page (the navbar one included), without a reload.

The avatar endpoint is cached for minutes, so the navbar keeps the old picture
unless the change reaches the ``<user-avatar>`` elements with a new URL.
"""

from __future__ import annotations

import io

from PIL import Image
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


class SettingsAvatarTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/users/settings#profile")
        self.page.wait_for_load_state("networkidle")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.navbar_picture = self.page.locator(
            f'label user-avatar[user-id="{self.user.id}"][size="sm"] img'
        )

    def _upload(self):
        self.page.locator('input[type="file"][accept^="image/"]').set_input_files(
            {"name": "me.png", "mimeType": "image/png", "buffer": _png_bytes()}
        )
        with self.page.expect_response(
            lambda r: r.request.method == "POST" and "/api/v1/users/me/avatar" in r.url
        ):
            self.page.get_by_role("button", name="Confirm").click()

    def test_uploading_a_picture_shows_it_in_the_navbar(self):
        # No picture yet: the 404 removed the image, the initials show.
        expect(self.navbar_picture).to_have_count(0)

        self._upload()

        expect(self.navbar_picture).to_have_count(1)
        self.page.wait_for_function(
            "(el) => el.complete && el.naturalWidth > 0",
            arg=self.navbar_picture.element_handle(),
        )

    def test_removing_the_picture_brings_the_initials_back_in_the_navbar(self):
        self._upload()
        expect(self.navbar_picture).to_have_count(1)

        with self.page.expect_response(
            lambda r: (
                r.request.method == "DELETE" and "/api/v1/users/me/avatar" in r.url
            )
        ):
            self.page.get_by_role("button", name="Remove").click()

        expect(self.navbar_picture).to_have_count(0)
