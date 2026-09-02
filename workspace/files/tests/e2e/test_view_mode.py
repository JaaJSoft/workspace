"""E2E test: the listing is rendered in the saved view mode only, and the
toggle switches by saving the preference and re-rendering.

Only one of the two layouts is in the DOM at a time, so the table controls
(filter, select all, actions) have to work against mosaic cards as well as
against table rows.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File
from workspace.users.services.settings import get_setting, set_setting


class ViewModeTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        for name in ("alpha.txt", "beta.txt", "gamma.txt"):
            File.objects.create(
                owner=self.user,
                name=name,
                node_type=File.NodeType.FILE,
                mime_type="text/plain",
            )

    def _open_files(self):
        self.login_as(self.user)
        with self.page.expect_response(
            lambda r: r.request.method == "POST" and "/api/v1/files/actions" in r.url
        ) as actions:
            self.page.goto(f"{self.live_server_url}/files")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        return actions.value

    def test_mosaic_mode_renders_cards_only_and_keeps_the_controls_working(self):
        set_setting(self.user, "files", "preferences", {"defaultViewMode": "mosaic"})
        actions = self._open_files()

        cards = self.page.locator("div.grid > div[data-uuid]")
        expect(cards).to_have_count(3)
        expect(self.page.locator("#folder-browser table")).to_have_count(0)
        # Actions were fetched for the cards, not for absent rows.
        self.assertEqual(len(actions.json()["files"]), 3)

        # The filter hides cards in place.
        self.page.locator("#folder-browser").get_by_placeholder("Filter by name").fill(
            "beta"
        )
        expect(cards.filter(has_text="beta.txt")).to_be_visible()
        expect(cards.filter(has_text="alpha.txt")).to_be_hidden()
        expect(self.page.locator("#folder-browser")).to_contain_text("1 of 3 items")

    def test_keyboard_shortcuts_act_on_a_selected_card(self):
        # Rows used to sit hidden behind the mosaic, so the shortcuts found
        # their target in the table. Now the card is all there is.
        set_setting(self.user, "files", "preferences", {"defaultViewMode": "mosaic"})
        folder = File.objects.create(
            owner=self.user, name="Reports", node_type=File.NodeType.FOLDER
        )
        self._open_files()

        card = self.page.locator(f'div.grid > div[data-uuid="{folder.uuid}"]')
        card.locator('input[type="checkbox"]').click()
        expect(self.page.locator("#folder-browser")).to_contain_text("1 selected")

        self.page.keyboard.press("Enter")
        expect(self.page.locator("#folder-browser h1")).to_have_text("Reports")

    def test_shift_range_skips_filtered_cards(self):
        set_setting(self.user, "files", "preferences", {"defaultViewMode": "mosaic"})
        File.objects.filter(owner=self.user).delete()
        cards = {}
        for name in ("art.txt", "bee.txt", "cart.txt"):
            cards[name] = File.objects.create(
                owner=self.user,
                name=name,
                node_type=File.NodeType.FILE,
                mime_type="text/plain",
            )
        self._open_files()

        # "rt" keeps art and cart on screen and hides bee, which sits between them.
        self.page.locator("#folder-browser").get_by_placeholder("Filter by name").fill(
            "rt"
        )
        bee = self.page.locator(f'div.grid > div[data-uuid="{cards["bee.txt"].uuid}"]')
        expect(bee).to_be_hidden()

        self.page.locator(
            f'div.grid > div[data-uuid="{cards["art.txt"].uuid}"] input[type="checkbox"]'
        ).click()
        self.page.locator(
            f'div.grid > div[data-uuid="{cards["cart.txt"].uuid}"] input[type="checkbox"]'
        ).click(modifiers=["Shift"])

        expect(self.page.locator("#folder-browser")).to_contain_text("2 selected")
        self.assertFalse(
            bee.locator('input[type="checkbox"]').evaluate("el => el.checked")
        )

    def test_toggle_saves_the_preference_and_re_renders(self):
        self._open_files()
        expect(self.page.locator("#folder-browser tbody tr[data-uuid]")).to_have_count(
            3
        )

        with self.page.expect_response(
            lambda r: (
                r.request.method == "PUT"
                and "/api/v1/settings/files/preferences" in r.url
            )
        ):
            self.page.get_by_role("button", name="Mosaic view").click()

        cards = self.page.locator("div.grid > div[data-uuid]")
        expect(cards).to_have_count(3)
        expect(self.page.locator("#folder-browser table")).to_have_count(0)
        self.assertEqual(
            get_setting(self.user, "files", "preferences")["defaultViewMode"], "mosaic"
        )

        # And back: the table returns and the preference follows.
        with self.page.expect_response(
            lambda r: (
                r.request.method == "PUT"
                and "/api/v1/settings/files/preferences" in r.url
            )
        ):
            self.page.get_by_role("button", name="List view").click()
        expect(self.page.locator("#folder-browser tbody tr[data-uuid]")).to_have_count(
            3
        )
        expect(cards).to_have_count(0)
        self.assertEqual(
            get_setting(self.user, "files", "preferences")["defaultViewMode"], "list"
        )
