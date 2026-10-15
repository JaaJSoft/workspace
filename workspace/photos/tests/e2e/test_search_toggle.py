"""E2E cover for the description search on a phone: an icon that opens the
field over the header and folds back. Skipped unless E2E=1 is set."""

from __future__ import annotations

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.photos.tests.embeddings import RED, embeddings_on, library_photo


@embeddings_on
class MobileSearchToggleTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="photographer", is_staff=True)
        self.red = library_photo(self.user, "red.jpg", RED)
        self.login_as(self.user)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(f"{self.live_server_url}/photos?similar={self.red.pk}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.title = self.page.locator("#photos-content h1")
        self.field = self.page.get_by_test_id("photos-search")
        self.opener = self.page.get_by_test_id("photos-search-open")

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_the_icon_opens_the_field_in_place_of_the_title(self):
        expect(self.field).to_be_hidden()
        expect(self.title).to_have_text("Similar to red.jpg")

        self.opener.click()

        expect(self.field).to_be_focused()
        expect(self.title).to_be_hidden()
        expect(self.opener).to_be_hidden()

    def test_escape_and_the_close_button_fold_it_back(self):
        for close in (
            lambda: self.page.keyboard.press("Escape"),
            lambda: self.page.get_by_role("button", name="Close the search").click(),
        ):
            with self.subTest(close=close):
                self.opener.click()
                expect(self.field).to_be_visible()

                close()

                expect(self.field).to_be_hidden()
                expect(self.title).to_be_visible()

    def test_the_field_stays_open_on_a_wide_screen(self):
        self.page.set_viewport_size({"width": 1280, "height": 900})

        expect(self.field).to_be_visible()
        expect(self.opener).to_be_hidden()
        expect(self.title).to_be_visible()
