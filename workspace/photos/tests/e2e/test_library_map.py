"""E2E cover for the Map page: the library's located photos grouped on a map.

The Python tests pin the points, the area grid and the page config; these
check that the browser groups the points, splits a group on a click, opens a
lone photo, narrows to a period, and tears the map down with the page. Tile
requests never leave the machine. Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime

from django.core.cache import cache
from django.test import override_settings
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.photos.tests.images import make_photo
from workspace.users.services.settings import get_setting, set_setting

TILE_HOST = "tiles.e2e.test"
BLANK_TILE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
MAP = "[data-testid='library-map']"
GROUPS = f"{MAP} .leaflet-marker-icon:has(.is-group)"
LONE = f"{MAP} .leaflet-marker-icon:not(:has(.is-group))"
GRID = "#map-photos #timeline-grid [data-uuid]"

COUNT_LIVE_MAPS = """() => {
  window.__liveMaps = 0;
  L.Map.addInitHook(function () {
    window.__liveMaps += 1;
    const remove = this.remove;
    this.remove = function () {
      window.__liveMaps -= 1;
      return remove.apply(this, arguments);
    };
  });
}"""


@override_settings(
    PHOTOS_MAP_TILE_URL=f"https://{TILE_HOST}/{{z}}/{{x}}/{{y}}.png",
    PHOTOS_MAP_TILE_ATTRIBUTION="Test tiles",
    PHOTOS_MAP_TILE_URL_DARK="",
)
class LibraryMapTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        # Photos is a preview module: staff see it in the navigation.
        self.user = self.create_user(username="traveller", is_staff=True)
        # Two photos a street apart, one across town, one in Rome.
        self.louvre = make_photo(
            self.user,
            "louvre.jpg",
            datetime(2024, 7, 14, 12, tzinfo=UTC),
            latitude=48.8606,
            longitude=2.3376,
        )
        self.tuileries = make_photo(
            self.user,
            "tuileries.jpg",
            datetime(2024, 7, 14, 13, tzinfo=UTC),
            latitude=48.8634,
            longitude=2.3275,
        )
        self.tower = make_photo(
            self.user,
            "tower.jpg",
            datetime(2023, 5, 2, 12, tzinfo=UTC),
            latitude=48.8584,
            longitude=2.2945,
        )
        self.rome = make_photo(
            self.user,
            "rome.jpg",
            datetime(2022, 3, 1, 12, tzinfo=UTC),
            latitude=41.8902,
            longitude=12.4922,
        )
        self.tile_referers = []

        def _stub_tile(route):
            self.tile_referers.append(route.request.all_headers().get("referer"))
            route.fulfill(status=200, content_type="image/png", body=BLANK_TILE)

        self.context.route(f"https://{TILE_HOST}/**", _stub_tile)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open_map(self, query=""):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/photos/map{query}")

    def test_offered_then_drawn_once_turned_on(self):
        self._open_map()

        offer = self.page.locator("[data-library-map-offer]")
        expect(offer).to_contain_text(f"loaded from {TILE_HOST}")
        expect(self.page.locator(MAP)).to_have_count(0)

        offer.get_by_role("button", name="Show the map").click()

        expect(self.page.locator(GROUPS).first).to_be_visible()
        self.assertTrue(get_setting(self.user, "photos", "show_map"))
        expect(self.page.locator("[data-testid='map-photos-count']")).to_have_text(
            "4 photos"
        )
        expect(self.page.locator(GRID)).to_have_count(4)

    def test_a_group_splits_as_the_map_zooms_into_it(self):
        set_setting(self.user, "photos", "show_map", True)
        self._open_map("?lat=46&lng=7&z=4")

        group = self.page.locator(GROUPS).first
        expect(group).to_contain_text("3")
        expect(self.page.locator(LONE)).to_have_count(1)

        group.click()

        # Zoomed in until Paris splits: no group of three any more.
        expect(self.page.locator(f"{GROUPS}:has-text('3')")).to_have_count(0)
        expect(self.page).to_have_url(re.compile(r"[?&]z=(\d{2}|[5-9])\b"))
        expect(self.page.locator("[data-testid='map-photos-count']")).not_to_have_text(
            "4 photos"
        )

    def test_a_lone_photo_opens_in_the_viewer(self):
        set_setting(self.user, "photos", "show_map", True)
        self._open_map("?lat=46&lng=7&z=4")

        self.page.locator(LONE).first.click()

        expect(self.page).to_have_url(re.compile(rf"open={self.rome.uuid}"))

    def test_the_period_narrows_the_map_and_the_grid(self):
        set_setting(self.user, "photos", "show_map", True)
        self._open_map("?lat=46&lng=7&z=4")
        expect(self.page.locator(GRID)).to_have_count(4)
        expect(
            self.page.locator("[data-testid='library-map-period-label']")
        ).to_have_text("Mar 2022 - Jul 2024")

        # From May 2023 on: Rome is left out.
        self.page.locator("[data-testid='library-map-period-from']").evaluate(
            """(el) => {
                el.value = 14;
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }"""
        )

        expect(
            self.page.locator("[data-testid='library-map-period-label']")
        ).to_have_text("May 2023 - Jul 2024")
        expect(self.page.locator(LONE)).to_have_count(0)
        expect(self.page.locator(GRID)).to_have_count(3)
        expect(self.page).to_have_url(re.compile(r"from=2023-05&to=2024-07"))

    def test_tile_requests_name_the_instance_and_nothing_more(self):
        # The origin only: the page's path and query carry the map's view.
        set_setting(self.user, "photos", "show_map", True)
        self._open_map("?lat=46&lng=7&z=4")

        expect(self.page.locator(f"{MAP} .leaflet-tile-loaded").first).to_be_attached()
        self.assertTrue(self.tile_referers)
        self.assertEqual(set(self.tile_referers), {f"{self.live_server_url}/"})

    def test_the_map_lives_and_dies_with_the_page(self):
        set_setting(self.user, "photos", "show_map", True)
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/photos")
        self.page.wait_for_selector("#timeline-grid [data-uuid]")
        self.page.evaluate(COUNT_LIVE_MAPS)

        self.page.locator("#photos-nav").get_by_role("link", name="Map").click()
        expect(self.page.locator(GROUPS).first).to_be_visible()
        self.assertEqual(self.page.evaluate("window.__liveMaps"), 1)

        self.page.locator("#photos-nav").get_by_role("link", name="Timeline").click()
        expect(self.page.locator(MAP)).to_have_count(0)
        self.assertEqual(self.page.evaluate("window.__liveMaps"), 0)
