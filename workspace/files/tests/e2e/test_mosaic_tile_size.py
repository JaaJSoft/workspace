"""E2E: the mosaic tile size slider resizes the cards and remembers the size."""

from __future__ import annotations

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File
from workspace.users.services.settings import get_setting, set_setting

CARD = "#folder-browser [data-uuid][data-node-type='file']"


class MosaicTileSizeTests(PlaywrightTestCase):
    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_slider_resizes_the_cards_and_saves_the_size(self):
        user = self.create_user(username="mosaic")
        set_setting(
            user,
            "files",
            "preferences",
            {"defaultViewMode": "mosaic", "mosaicTileSize": 1},
        )
        for i in range(3):
            File.objects.create(
                owner=user, name=f"f{i}.txt", node_type=File.NodeType.FILE
            )
        self.login_as(user)
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(f"{self.live_server_url}/files")
        card = self.page.locator(CARD).first
        expect(card).to_be_visible()
        small = card.bounding_box()["width"]

        slider = self.page.get_by_title("Tile size")
        slider.fill("5")

        self.page.wait_for_function(
            "([sel, before]) => document.querySelector(sel).offsetWidth > before + 50",
            arg=[CARD, small],
        )
        self.page.wait_for_function(
            "() => fetch('/api/v1/settings/files/preferences')"
            ".then(r => r.json()).then(d => (window.__saved = d.value.mosaicTileSize))"
            " && window.__saved === 5"
        )
        cache.clear()
        self.assertEqual(get_setting(user, "files", "preferences")["mosaicTileSize"], 5)
