"""E2E cover for the Similar photos page: its way back, which only the
browser's history can prove. Skipped unless E2E=1 is set."""

from __future__ import annotations

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.photos.models import Album, AlbumItem
from workspace.photos.tests.embeddings import (
    BLUE,
    DARK_RED,
    RED,
    embeddings_on,
    library_photo,
)

TILES = "#timeline-grid [data-uuid]"


@embeddings_on
class SimilarPhotosBackTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="photographer", is_staff=True)
        self.red = library_photo(self.user, "red.jpg", RED)
        library_photo(self.user, "dark.jpg", DARK_RED)
        library_photo(self.user, "blue.jpg", BLUE)
        self.login_as(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open(self, path):
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.wait_for_selector(TILES)

    def _show_similar(self):
        self.page.locator(f"{TILES}[data-uuid='{self.red.pk}']").click(button="right")
        self.page.locator("#photos-context-menu").get_by_role(
            "button", name="Similar photos"
        ).click()
        expect(self.page.locator("#photos-content h1")).to_have_text(
            "Similar to red.jpg"
        )

    def test_back_returns_to_the_page_the_menu_was_opened_on(self):
        album = Album.objects.create(owner=self.user, title="Trip")
        AlbumItem.objects.create(
            album=album, file=self.red, added_by=self.user, position=0
        )
        self._open("/photos")
        self.page.locator(f"#photos-nav [data-album='{album.uuid}']").click()
        expect(self.page.locator("#photos-content h1")).to_have_text("Trip")
        album_url = self.page.url
        self._show_similar()

        self.page.get_by_test_id("photos-similar-back").click()

        expect(self.page).to_have_url(album_url)
        expect(self.page.locator("#photos-content h1")).to_have_text("Trip")

    def test_back_to_the_first_page_loaded_shows_that_page(self):
        self._open("/photos")
        self._show_similar()

        for go_back in (
            lambda: self.page.get_by_test_id("photos-similar-back").click(),
            self.page.go_back,
        ):
            with self.subTest(go_back=go_back):
                go_back()
                expect(self.page).to_have_url(f"{self.live_server_url}/photos")
                expect(self.page.locator("#photos-content h1")).to_have_text("Timeline")
                self._show_similar()

    def test_opened_directly_back_goes_to_the_timeline(self):
        self._open(f"/photos?similar={self.red.pk}&scope=all")

        self.page.get_by_test_id("photos-similar-back").click()

        expect(self.page).to_have_url(f"{self.live_server_url}/photos?scope=all")
        expect(self.page.locator("#photos-content h1")).to_have_text("Timeline")
