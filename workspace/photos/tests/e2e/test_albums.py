"""E2E cover for albums: what only a rendered page can prove.

The API and view tests pin what the server stores and sends; these check
that the selection really gathers tiles (check mark, shift-click), that the
picker, the tile menu and the album menu drive the endpoints, and that a
manual album reorders by dragging. Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.photos.models import Album, AlbumItem
from workspace.photos.services.albums import create_album
from workspace.photos.tests.images import make_photo

TILES = "#timeline-grid [data-uuid]"


class PhotosAlbumsTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        # Photos is a preview module: staff see it in the navigation.
        self.user = self.create_user(username="photographer", is_staff=True)
        self.photos = [
            make_photo(
                self.user, f"day{day}.jpg", datetime(2024, 7, day, 12, tzinfo=UTC)
            )
            for day in (14, 13, 12, 11)
        ]
        self.login_as(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open(self, path="/photos"):
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.wait_for_selector(TILES)

    def _tile(self, index):
        return self.page.locator(TILES).nth(index)

    def _order(self, album):
        return list(
            AlbumItem.objects.filter(album=album)
            .order_by("position", "file_id")
            .values_list("file_id", flat=True)
        )

    def _prompt(self, value):
        self.page.locator("#app-dialog-prompt-input").fill(value)
        self.page.locator("#app-dialog-prompt-ok").click()

    def test_selecting_a_range_and_adding_it_to_an_album(self):
        album = create_album(self.user, "Summer")
        self._open()

        first = self._tile(0)
        first.hover()
        first.locator("[data-select]").click()
        # Once something is selected a click on a tile selects it too, and
        # shift reaches every tile in between.
        self._tile(2).click(modifiers=["Shift"])

        expect(self.page.locator("[data-selection-count]")).to_have_text("3 selected")
        expect(self.page.locator(f"{TILES}[data-selected='1']")).to_have_count(3)
        expect(self.page.locator("dialog.modal[open]")).to_have_count(0)

        self.page.locator("[data-selection-bar]").get_by_role(
            "button", name="Add to album"
        ).click()
        self.page.locator(f"[data-picker-album='{album.uuid}']").click()

        expect(self.page.locator("[data-selection-bar]")).to_be_hidden()
        expect(
            self.page.locator(f"#photos-nav [data-album='{album.uuid}']")
        ).to_contain_text("3")
        self.assertEqual(self._order(album), [p.pk for p in self.photos[:3]])

    def test_escape_clears_the_selection(self):
        self._open()
        first = self._tile(0)
        first.hover()
        first.locator("[data-select]").click()
        expect(self.page.locator("[data-selection-bar]")).to_be_visible()

        self.page.keyboard.press("Escape")

        expect(self.page.locator("[data-selection-bar]")).to_be_hidden()
        expect(first).to_have_attribute("data-selected", "0")

    def test_new_album_from_the_tile_menu_picker(self):
        self._open()
        tile = self._tile(1)
        tile.hover()
        tile.get_by_role("button", name="More actions").click()
        self.page.locator("#photos-context-menu").get_by_text("Add to album").click()

        self.page.locator("[data-picker-new]").click()
        self._prompt("Beach day")

        album = self._wait_for_album("Beach day")
        self.assertEqual(self._order(album), [self.photos[1].pk])
        expect(
            self.page.locator(f"#photos-nav [data-album='{album.uuid}']")
        ).to_be_visible()

    def _wait_for_album(self, title):
        self.page.wait_for_function(
            "(t) => [...document.querySelectorAll('#photos-nav [data-album]')]"
            ".some(a => a.textContent.includes(t))",
            arg=title,
        )
        return Album.objects.get(title=title)

    def test_new_album_from_the_sidebar_opens_it(self):
        self._open()

        self.page.locator("#photos-nav").get_by_role("button", name="New album").click()
        self._prompt("Empty for now")

        self.page.wait_for_url("**/photos/albums/**")
        expect(self.page.get_by_text("This album is empty")).to_be_visible()
        album = Album.objects.get(title="Empty for now")
        self.assertIn(str(album.uuid), self.page.url)

    def test_remove_from_the_album_and_set_the_cover(self):
        album = create_album(self.user, "Summer", files=self.photos[:3])
        self._open(f"/photos/albums/{album.uuid}")
        expect(self.page.locator("#photos-header")).to_contain_text("3 photos")

        tile = self._tile(2)
        cover_uuid = tile.get_attribute("data-uuid")
        tile.hover()
        tile.get_by_role("button", name="More actions").click()
        self.page.locator("#photos-context-menu").get_by_text("Set as cover").click()

        expect(self.page.locator("[data-album-cover]")).to_have_attribute(
            "data-album-cover", cover_uuid
        )

        tile = self._tile(0)
        removed = tile.get_attribute("data-uuid")
        tile.hover()
        tile.get_by_role("button", name="More actions").click()
        self.page.locator("#photos-context-menu").get_by_text(
            "Remove from album"
        ).click()

        expect(self.page.locator(f"{TILES}[data-uuid='{removed}']")).to_have_count(0)
        expect(self.page.locator("#photos-header")).to_contain_text("2 photos")
        self.assertEqual(len(self._order(album)), 2)

    def test_rename_and_delete_from_the_album_menu(self):
        album = create_album(self.user, "Summer", files=self.photos[:1])
        self._open(f"/photos/albums/{album.uuid}")

        self.page.get_by_role("button", name="Album actions").click()
        self.page.locator("#photos-album-menu").get_by_text("Rename").click()
        self._prompt("Summer 2024")

        expect(self.page.locator("#photos-header h1")).to_have_text("Summer 2024")

        self.page.get_by_role("button", name="Album actions").click()
        self.page.locator("#photos-album-menu").get_by_text("Delete album").click()
        self.page.locator("#app-dialog-confirm-ok").click()

        self.page.wait_for_url(f"{self.live_server_url}/photos")
        self.assertFalse(Album.objects.filter(pk=album.pk).exists())
        self.photos[0].refresh_from_db()
        self.assertIsNone(self.photos[0].deleted_at)

    def test_a_manual_album_reorders_by_dragging(self):
        album = create_album(
            self.user, "Trip", files=self.photos, sort_mode=Album.SortMode.MANUAL
        )
        self._open(f"/photos/albums/{album.uuid}")
        self.page.evaluate(
            """() => {
                window.__effects = {};
                window.addEventListener('dragstart', (e) => {
                    window.__effects.allowed = e.dataTransfer.effectAllowed;
                });
                window.addEventListener('dragover', (e) => {
                    if (e.target.closest('[data-uuid]')) {
                        window.__effects.drop = e.dataTransfer.dropEffect;
                    }
                });
            }"""
        )
        # The album's actions load after the page: reorder must be known
        # before the drag starts.
        self.page.wait_for_load_state("networkidle")

        source = self._tile(0).bounding_box()
        target = self._tile(3).bounding_box()
        self.page.mouse.move(source["x"] + 40, source["y"] + 40)
        self.page.mouse.down()
        self.page.mouse.move(source["x"] + 40, source["y"] + 50, steps=5)
        # The right half of the last tile: after it.
        self.page.mouse.move(
            target["x"] + target["width"] - 10, target["y"] + 40, steps=20
        )
        self.page.wait_for_timeout(200)
        self.page.mouse.move(
            target["x"] + target["width"] - 8, target["y"] + 42, steps=3
        )
        effects = self.page.evaluate("window.__effects")
        self.page.mouse.up()

        # Playwright's synthesized drags allow every effect: only the values
        # each side negotiated prove a real browser accepts the drop.
        self.assertEqual(effects["allowed"], "move")
        self.assertEqual(effects["drop"], "move")
        first, *rest = self.photos
        expected = [str(p.uuid) for p in [*rest, first]]
        self.page.wait_for_function(
            "(expected) => JSON.stringify([...document.querySelectorAll("
            "'#timeline-grid [data-uuid]')].map(t => t.dataset.uuid))"
            " === JSON.stringify(expected)",
            arg=expected,
        )
        self.assertEqual(self._order(album), [p.pk for p in [*rest, first]])
