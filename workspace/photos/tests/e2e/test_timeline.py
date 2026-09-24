"""E2E cover for the photo timeline: what only a rendered page can prove.

The view tests pin what the server sends; these check that the sentinel
really pulls the next page into the same grid, that a tile opens the Files
viewer, and that the sidebar swaps the listing in place. Skipped unless E2E=1
is set.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileFavorite
from workspace.files.services import FileService
from workspace.photos.tests.images import make_photo
from workspace.users.services.settings import set_setting

TILES = "#timeline-grid [data-uuid]"


class PhotosTimelineTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        # Photos is a preview module: staff see it in the navigation, which
        # is what gives the page its module and its sidebar preference.
        self.user = self.create_user(username="photographer", is_staff=True)
        self.photos = [
            make_photo(
                self.user, f"day{day}.jpg", datetime(2024, 7, day, 12, tzinfo=UTC)
            )
            for day in (14, 13, 12, 11, 10)
        ]
        self.undated = make_photo(self.user, "scan.png", None)
        self.login_as(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open(self, path="/photos"):
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.wait_for_selector(TILES)

    def test_timeline_groups_photos_by_month_and_day(self):
        self._open()

        expect(self.page.locator("#timeline-grid [data-month]")).to_have_text(
            ["July 2024"]
        )
        expect(self.page.locator("#timeline-grid [data-day] h3")).to_have_count(5)
        expect(self.page.locator("#timeline-grid [data-undated]")).to_be_visible()
        expect(self.page.locator(TILES)).to_have_count(6)

    @patch("workspace.photos.services.timeline.PAGE_SIZE", 2)
    def test_scrolling_appends_the_next_pages_to_the_same_grid(self):
        self.page.set_viewport_size({"width": 1280, "height": 400})
        self._open()
        expect(self.page.locator(TILES)).not_to_have_count(6)

        for _ in range(4):
            self.page.evaluate(
                "document.getElementById('photos-content').scrollTo(0, 1e9)"
            )
            self.page.wait_for_timeout(300)

        expect(self.page.locator(TILES)).to_have_count(6)
        uuids = self.page.eval_on_selector_all(
            TILES, "tiles => tiles.map(t => t.dataset.uuid)"
        )
        self.assertEqual(uuids, [str(f.uuid) for f in [*self.photos, self.undated]])
        # One grid, one month header, one Undated header: nothing repeated.
        expect(self.page.locator("#timeline-grid")).to_have_count(1)
        expect(self.page.locator("#timeline-grid [data-month]")).to_have_count(1)
        expect(self.page.locator("#timeline-grid [data-undated]")).to_have_count(1)
        expect(self.page.locator("#timeline-more [x-data]")).to_have_count(0)

    def test_a_tile_opens_the_files_viewer(self):
        self._open()

        self.page.locator(TILES).first.click()

        dialog = self.page.locator("dialog.modal[open]")
        expect(dialog).to_be_visible()
        expect(dialog.locator("h3")).to_have_text("day14.jpg")
        expect(self.page.locator("#viewer-panel img").first).to_be_visible()
        self.assertIn(f"open={self.photos[0].uuid}", self.page.url)

        self.page.keyboard.press("ArrowRight")
        expect(dialog.locator("h3")).to_have_text("day13.jpg")

    def test_opening_a_search_hit_lands_on_the_photo(self):
        target = self.photos[2]

        self.page.goto(
            f"{self.live_server_url}/photos?date=2024-07-12&open={target.uuid}"
        )

        expect(self.page.locator("dialog.modal[open] h3")).to_have_text("day12.jpg")
        expect(self.page.locator(TILES).first).to_have_attribute(
            "data-uuid", str(target.uuid)
        )

    def test_sidebar_swaps_the_listing_in_place(self):
        FileFavorite.objects.create(owner=self.user, file=self.photos[1])
        self._open()

        self.page.locator("#photos-nav a[href='/photos?favorites=1']").click()

        expect(self.page).to_have_url(f"{self.live_server_url}/photos?favorites=1")
        expect(self.page.locator(TILES)).to_have_count(1)
        expect(self.page.locator("#photos-content h1")).to_have_text("Favorites")

    def test_collapsed_sidebar_is_the_rail_from_the_first_paint(self):
        set_setting(self.user, "photos", "sidebar_collapsed", True)
        self.context.add_init_script(
            """
            window.__firstAsideClass = null;
            new MutationObserver(() => {
              const aside = document.querySelector('.drawer-side aside');
              if (aside && window.__firstAsideClass === null) {
                window.__firstAsideClass = aside.className;
              }
            }).observe(document, { childList: true, subtree: true });
            """
        )

        self._open()

        first = self.page.evaluate("window.__firstAsideClass").split()
        self.assertIn("w-16", first)
        self.assertNotIn("w-72", first)
        box = self.page.locator(".drawer-side aside").bounding_box()
        self.assertEqual(round(box["width"]), 64)

    def _tile_width(self):
        return round(self.page.locator(TILES).first.bounding_box()["width"])

    def test_the_size_slider_resizes_the_tiles_and_remembers_the_size(self):
        set_setting(self.user, "photos", "tile_size", 1)
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self._open()
        self.assertEqual(self._tile_width(), 96)

        self.page.get_by_title("Tile size").fill("5")

        self.page.wait_for_function(
            "(sel) => document.querySelector(sel).offsetWidth === 256", arg=TILES
        )
        self.page.wait_for_function(
            "() => fetch('/api/v1/settings/photos/tile_size')"
            ".then(r => r.json()).then(d => (window.__saved = d.value))"
            " && window.__saved === 5"
        )
        # A sidebar navigation swaps the grid, not the size.
        self.page.locator("#photos-nav a[href='/photos?favorites=1']").click()
        expect(self.page).to_have_url(f"{self.live_server_url}/photos?favorites=1")
        self.page.locator("#photos-nav a[href='/photos']").first.click()
        expect(self.page.locator(TILES)).to_have_count(6)
        self.assertEqual(self._tile_width(), 256)
        expect(self.page.get_by_title("Tile size")).to_have_value("5")

    def test_scope_tabs_switch_the_library_in_place(self):
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        bob = self.create_user(username="bob")
        root = FileService.create_folder(owner=bob, name="Family", group=family)
        shared = make_photo(
            bob, "family.jpg", datetime(2024, 7, 9, 12, tzinfo=UTC), parent=root
        )
        self._open()
        tabs = self.page.locator("nav[aria-label='Library'] a")
        expect(tabs).to_have_text(["Mine", "All", "Family"])

        tabs.nth(2).click()

        expect(self.page).to_have_url(
            f"{self.live_server_url}/photos?scope=group%3A{family.pk}"
        )
        expect(self.page.locator(TILES)).to_have_count(1)
        expect(self.page.locator(TILES).first).to_have_attribute(
            "data-uuid", str(shared.uuid)
        )
        expect(tabs.nth(2)).to_have_attribute("aria-current", "page")

        self.page.locator("nav[aria-label='Library'] a", has_text="All").click()

        expect(self.page.locator(TILES)).to_have_count(7)

    def _open_menu(self, index=0):
        tile = self.page.locator(TILES).nth(index)
        tile.hover()
        tile.get_by_role("button", name="More actions").click()
        menu = self.page.locator("#photos-context-menu")
        expect(menu).to_be_visible()
        expect(menu.get_by_role("button", name="Properties")).to_be_visible()
        return menu

    def test_more_actions_opens_the_files_properties_panel(self):
        self._open()

        self._open_menu().get_by_role("button", name="Properties").click()

        panel = self.page.locator("#properties-sidebar")
        expect(panel).to_be_visible()
        expect(panel.locator("#properties-content")).to_contain_text("day14.jpg")
        expect(self.page.locator("#photos-context-menu")).to_be_hidden()

        panel.get_by_role("button").first.click()
        expect(panel).to_be_hidden()

    def test_renaming_from_the_menu_renames_the_tile_and_nothing_else(self):
        File.objects.filter(pk=self.photos[0].pk).update(has_thumbnail=True)
        self._open()
        tile = self.page.locator(TILES).first

        self._open_menu().get_by_role("button", name="Rename").click()
        dialog = self.page.locator("#rename-dialog")
        dialog.locator("input").fill("sunset.jpg")
        dialog.get_by_role("button", name="Rename").click()

        expect(tile).to_have_attribute("data-display-name", "sunset.jpg")
        expect(tile.locator("button").first).to_have_attribute("title", "sunset.jpg")
        expect(tile.locator("img")).to_have_attribute("alt", "sunset.jpg")
        expect(tile.locator("[aria-haspopup='menu']")).to_have_attribute(
            "aria-label", "More actions for sunset.jpg"
        )
        for label in ("Add to favorites", "Remove from favorites"):
            expect(tile.locator(f"[aria-label='{label}']")).to_have_attribute(
                "title", label
            )

    def test_right_click_opens_the_same_menu(self):
        self._open()

        self.page.locator(TILES).first.click(button="right")

        menu = self.page.locator("#photos-context-menu")
        expect(menu).to_be_visible()
        expect(menu.get_by_role("link", name="Open in Files")).to_have_attribute(
            "href", f"/files?open={self.photos[0].uuid}"
        )

    def test_favoriting_from_the_menu_stars_the_tile(self):
        self._open()
        tile = self.page.locator(TILES).first
        expect(tile).to_have_attribute("data-favorite", "0")

        self._open_menu().get_by_role("button", name="Add to favorites").click()

        expect(tile).to_have_attribute("data-favorite", "1")
        self.assertTrue(
            FileFavorite.objects.filter(owner=self.user, file=self.photos[0]).exists()
        )

    def test_the_tile_star_toggles_the_favorite(self):
        self._open()
        tile = self.page.locator(TILES).first
        tile.hover()

        tile.get_by_role("button", name="Add to favorites").click()

        expect(tile).to_have_attribute("data-favorite", "1")
        expect(tile.get_by_role("button", name="Remove from favorites")).to_be_visible()
        self.assertTrue(
            FileFavorite.objects.filter(owner=self.user, file=self.photos[0]).exists()
        )

        tile.get_by_role("button", name="Remove from favorites").click()

        expect(tile).to_have_attribute("data-favorite", "0")
        self.assertFalse(
            FileFavorite.objects.filter(owner=self.user, file=self.photos[0]).exists()
        )
