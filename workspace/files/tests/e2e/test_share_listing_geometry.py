"""E2E: the public listing's icons and tiles have the geometry they claim.

The list row once put a square grid tile, whose default icon is 48px, in
a 40px box: the flex container squeezed the width and not the height, so
every icon rendered 40x48, stretched and clipped by ``overflow-hidden``.
Class strings say nothing about that; the template was "correct" and the
page was wrong. What pins it down is a measurement.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

from ._share_fixtures import build_shared_tree


class SharedListingGeometryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        build_shared_tree(self)

    def _url(self, view):
        return f"{self.live_server_url}/files/shared/{self.link.token}?view={view}"

    def test_list_row_icons_are_square_and_at_row_scale(self):
        self.page.goto(self._url("list"))
        icons = self.page.locator("#shared-listing li > svg")
        expect(icons).to_have_count(2)
        for icon in icons.all():
            box = icon.bounding_box()
            self.assertIsNotNone(box)
            self.assertAlmostEqual(box["width"], box["height"], delta=1)
            # w-5: 20px. The bug rendered 40x48.
            self.assertAlmostEqual(box["width"], 20, delta=1)

    def test_grid_tiles_are_square(self):
        self.page.goto(self._url("grid"))
        tiles = self.page.locator("#shared-listing .grid > a > div.relative")
        expect(tiles).to_have_count(2)
        for tile in tiles.all():
            box = tile.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreater(box["width"], 60)
            self.assertAlmostEqual(box["width"], box["height"], delta=1)
