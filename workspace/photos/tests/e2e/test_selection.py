"""E2E cover for what a selection of photos can have done to it.

The JS tests pin which actions the selection offers and what each one sends;
these check that the bar and the right-click menu reach the Files endpoints
from a rendered page. Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileFavorite
from workspace.photos.tests.images import make_photo

TILES = "#timeline-grid [data-uuid]"
BAR = "[data-selection-bar]"


class PhotosSelectionTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        # Photos is a preview module: staff see it in the navigation.
        self.user = self.create_user(username="photographer", is_staff=True)
        self.photos = [
            make_photo(
                self.user, f"day{day}.jpg", datetime(2024, 7, day, 12, tzinfo=UTC)
            )
            for day in (14, 13, 12)
        ]
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/photos")
        self.page.wait_for_selector(TILES)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _tile(self, index):
        return self.page.locator(TILES).nth(index)

    def _select_first(self, count):
        first = self._tile(0)
        first.hover()
        first.locator("[data-select]").click()
        if count > 1:
            self._tile(count - 1).click(modifiers=["Shift"])
        expect(self.page.locator("[data-selection-count]")).to_have_text(
            f"{count} selected"
        )

    def _bar_button(self, name):
        return self.page.locator(BAR).get_by_role("button", name=name, exact=True)

    def test_select_all_and_favorite_from_the_bar(self):
        self._select_first(1)
        self.page.keyboard.press("Control+a")
        expect(self.page.locator("[data-selection-count]")).to_have_text("3 selected")

        add = self._bar_button("Add to favorites")
        expect(add).to_be_enabled()
        add.click()

        expect(self.page.locator(BAR)).to_be_hidden()
        expect(self.page.locator(f"{TILES}[data-favorite='1']")).to_have_count(3)
        self.assertEqual(
            FileFavorite.objects.filter(owner=self.user).count(), len(self.photos)
        )

        # Every one a favorite now: the same button takes them back out.
        self._select_first(2)
        remove = self._bar_button("Remove from favorites")
        expect(remove).to_be_enabled()
        remove.click()
        expect(self.page.locator(f"{TILES}[data-favorite='1']")).to_have_count(1)

    def test_trash_from_the_right_click_menu_of_the_selection(self):
        self._select_first(2)

        self._tile(1).click(button="right")
        menu = self.page.locator("#photos-selection-menu")
        expect(menu).to_be_visible()
        expect(menu).to_contain_text("2 photos selected")
        menu.get_by_text("Move to trash").click()
        self.page.locator("#app-dialog-confirm-ok").click()

        expect(self.page.locator(TILES)).to_have_count(1)
        # The sidebar and header refresh that follows must land before the
        # teardown deletes the user it is rendered for.
        self.page.wait_for_load_state("networkidle")
        trashed = File.objects.filter(
            uuid__in=[p.uuid for p in self.photos[:2]], deleted_at__isnull=False
        )
        self.assertEqual(trashed.count(), 2)

    def test_download_the_selection_as_one_archive(self):
        self._select_first(2)
        download_button = self._bar_button("Download")
        expect(download_button).to_be_enabled()

        with self.page.expect_download() as info:
            download_button.click()

        download = info.value
        self.assertEqual(download.suggested_filename, "photos.zip")
        with zipfile.ZipFile(download.path()) as archive:
            self.assertEqual(sorted(archive.namelist()), ["day13.jpg", "day14.jpg"])
