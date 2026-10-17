"""E2E cover for the map of a photo's location in the properties panel.

The Python tests pin what the section renders; these check that Leaflet
mounts from a rendered page, puts its pin where the photo was taken, and is
torn down with the section on every swap. Tile requests never leave the
machine: they are answered by a stub. Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from django.core.cache import cache
from django.test import override_settings
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.photos.tests.images import make_photo
from workspace.users.services.settings import get_setting, set_setting

TILES = "#timeline-grid [data-uuid]"
TILE_HOST = "tiles.e2e.test"
# A 1x1 transparent PNG.
BLANK_TILE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

# Counts the Leaflet maps alive on the page and records every marker placed.
# addInitHook is Leaflet's own extension point, so nothing in photo_map.js
# exists for the test's sake.
INSTRUMENT_LEAFLET = """() => {
  window.__liveMaps = 0;
  window.__markers = [];
  L.Map.addInitHook(function () {
    window.__liveMaps += 1;
    const remove = this.remove;
    this.remove = function () {
      window.__liveMaps -= 1;
      return remove.apply(this, arguments);
    };
  });
  L.Marker.addInitHook(function () {
    const at = this.getLatLng();
    window.__markers.push([at.lat, at.lng]);
  });
}"""


@override_settings(
    PHOTOS_MAP_TILE_URL=f"https://{TILE_HOST}/{{z}}/{{x}}/{{y}}.png",
    PHOTOS_MAP_TILE_ATTRIBUTION="Test tiles",
)
class PhotoLocationMapTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        # Photos is a preview module: staff see it in the navigation.
        self.user = self.create_user(username="traveller", is_staff=True)
        self.paris = make_photo(
            self.user,
            "paris.jpg",
            datetime(2024, 7, 14, 12, tzinfo=UTC),
            latitude=48.8584,
            longitude=2.2945,
        )
        self.rome = make_photo(
            self.user,
            "rome.jpg",
            datetime(2024, 7, 13, 12, tzinfo=UTC),
            latitude=41.8902,
            longitude=12.4922,
        )
        self.tile_requests = []

        def _stub_tile(route):
            self.tile_requests.append(route.request.url)
            route.fulfill(status=200, content_type="image/png", body=BLANK_TILE)

        self.context.route(f"https://{TILE_HOST}/**", _stub_tile)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open_photos(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/photos")
        self.page.wait_for_selector(TILES)
        self.page.evaluate(INSTRUMENT_LEAFLET)

    def _open_properties(self, photo):
        tile = self.page.locator(f"{TILES}[data-uuid='{photo.uuid}']")
        tile.hover()
        tile.get_by_role("button", name="More actions").click()
        self.page.locator("#photos-context-menu").get_by_role(
            "button", name="Properties"
        ).click()
        content = self.page.locator("#properties-content")
        expect(content).to_contain_text(photo.name)
        return content

    def _last_marker(self):
        return self.page.evaluate("window.__markers[window.__markers.length - 1]")

    def test_the_map_pins_the_photo_and_one_map_survives_switching(self):
        set_setting(self.user, "photos", "show_map", True)
        self._open_photos()

        content = self._open_properties(self.paris)

        expect(content.locator(".leaflet-container")).to_have_count(1)
        expect(content.locator(".leaflet-marker-icon")).to_be_visible()
        expect(content.locator(".leaflet-control-attribution")).to_contain_text(
            "Test tiles"
        )
        self.assertEqual(self._last_marker(), [48.8584, 2.2945])
        expect(content.locator(".leaflet-tile-loaded").first).to_be_attached()
        self.assertTrue(self.tile_requests)

        for photo in (self.rome, self.paris, self.rome):
            content = self._open_properties(photo)
            expect(content.locator(".leaflet-marker-icon")).to_be_visible()

        expect(self.page.locator(".leaflet-container")).to_have_count(1)
        self.assertEqual(self.page.evaluate("window.__liveMaps"), 1)
        self.assertEqual(self._last_marker(), [41.8902, 12.4922])

    def test_nothing_reaches_the_tile_server_until_the_user_opts_in(self):
        self._open_photos()

        content = self._open_properties(self.paris)

        offer = content.locator("[data-photo-map-offer]")
        expect(offer).to_contain_text(f"loaded from {TILE_HOST}")
        expect(content.locator(".leaflet-container")).to_have_count(0)
        self.assertEqual(self.tile_requests, [])

        offer.get_by_role("button", name="Show on a map").click()

        expect(content.locator(".leaflet-marker-icon")).to_be_visible()
        self.assertEqual(self._last_marker(), [48.8584, 2.2945])
        self.assertIs(get_setting(self.user, "photos", "show_map"), True)

        content.get_by_role("button", name="Hide the map").click()

        expect(content.locator("[data-photo-map-offer]")).to_be_visible()
        expect(self.page.locator(".leaflet-container")).to_have_count(0)
        self.assertEqual(self.page.evaluate("window.__liveMaps"), 0)

    @override_settings(
        PHOTOS_MAP_TILE_URL_DARK=f"https://{TILE_HOST}/dark/{{z}}/{{x}}/{{y}}.png"
    )
    def test_the_dark_style_follows_the_theme(self):
        set_setting(self.user, "photos", "show_map", True)
        set_setting(self.user, "core", "theme", "dark")
        self._open_photos()

        content = self._open_properties(self.paris)

        expect(content.locator(".leaflet-tile-loaded").first).to_be_attached()
        self.assertTrue(self.tile_requests)
        self.assertTrue(all("/dark/" in url for url in self.tile_requests))

        # The theme toggle switches the page in place: no reload, same map.
        self.tile_requests.clear()
        self.page.evaluate(
            "document.documentElement.setAttribute('data-theme', 'light')"
        )

        expect(
            content.locator(f".leaflet-tile-loaded[src^='https://{TILE_HOST}/1']")
        ).not_to_have_count(0)
        self.assertTrue(self.tile_requests)
        self.assertFalse(any("/dark/" in url for url in self.tile_requests))
        self.assertEqual(self.page.evaluate("window.__liveMaps"), 1)
