"""E2E cover for what a listing item answers to, in both view modes.

A row or a mosaic card opens on a click (a folder navigates, a viewable
file opens the viewer, any other file does nothing), selects through its
checkbox without opening, toggles its favourite, opens the item menu from
its menu button and on a right-click, becomes the keyboard shortcuts'
target while hovered, dims while an action runs on it, and offers itself
for a drag once the registry allows a cut. In the trash, a viewable file
still opens the viewer, but a folder does not navigate and nothing drags.
"""

from __future__ import annotations

from django.core.cache import cache
from django.utils import timezone
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File
from workspace.users.services.settings import set_setting


class _ListingItemInteractions:
    VIEW_MODE = None

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        set_setting(
            self.user, "files", "preferences", {"defaultViewMode": self.VIEW_MODE}
        )
        self.folder = File.objects.create(
            owner=self.user, name="Reports", node_type=File.NodeType.FOLDER
        )
        self.blob = File.objects.create(
            owner=self.user,
            name="blob.bin",
            node_type=File.NodeType.FILE,
            mime_type="application/octet-stream",
        )
        self.note = File.objects.create(
            owner=self.user,
            name="notes.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

    def tearDown(self):
        cache.clear()
        super().tearDown()

    # --- helpers -----------------------------------------------------------

    def item(self, file_obj):
        if self.VIEW_MODE == "mosaic":
            return self.page.locator(f'div.grid > div[data-uuid="{file_obj.uuid}"]')
        return self.page.locator(f'tr[data-uuid="{file_obj.uuid}"]')

    def plain_spot(self, file_obj):
        """A part of the item that is neither a link, a button nor a checkbox."""
        if self.VIEW_MODE == "mosaic":
            return self.item(file_obj).locator(f'span[title="{file_obj.name}"]')
        return self.item(file_obj).locator('td[data-col="size"]')

    def open_listing(self, path="/files"):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.page.evaluate(
            """() => {
              window.__opened = [];
              window.addEventListener('open-file-viewer', (e) => {
                window.__opened.push(e.detail.uuid);
                e.stopImmediatePropagation();
              }, { capture: true });
            }"""
        )

    def wait_for_actions(self, *files):
        self.page.wait_for_function(
            """(uuids) => {
              const root = document.querySelector('[x-data*="fileTableWithView"]');
              const data = root && Alpine.$data(root);
              return !!data && uuids.every((u) => (data.actionsMap[u] || []).length > 0);
            }""",
            arg=[str(f.uuid) for f in files],
        )

    def opened(self):
        return self.page.evaluate("window.__opened")

    def menu_target(self):
        return self.page.evaluate(
            """() => {
              const menu = Alpine.$data(document.querySelector('[x-data*="contextMenu"]'));
              return menu.isOpen
                ? { uuid: menu.nodeData && menu.nodeData.uuid, background: menu.isBackgroundMenu }
                : null;
            }"""
        )

    def close_menu(self):
        self.page.evaluate(
            "Alpine.$data(document.querySelector('[x-data*=\"contextMenu\"]')).close()"
        )

    def browser_call(self, method, file_obj):
        self.page.evaluate(
            "([method, uuid]) => Alpine.$data(document.querySelector('[x-data=\"fileBrowser()\"]'))[method](uuid)",
            [method, str(file_obj.uuid)],
        )

    # --- tests -------------------------------------------------------------

    def test_a_click_opens_a_folder(self):
        self.open_listing()
        self.plain_spot(self.folder).click()
        expect(self.page.locator("#folder-browser h1")).to_have_text("Reports")

    def test_a_click_opens_a_viewable_file_and_nothing_else(self):
        self.open_listing()
        self.plain_spot(self.blob).click()
        self.plain_spot(self.note).click()
        self.page.wait_for_function("window.__opened.length > 0")
        self.assertEqual(self.opened(), [str(self.note.uuid)])

    def test_checkboxes_select_without_opening_and_shift_extends_the_range(self):
        self.open_listing()
        self.item(self.folder).locator("input[data-select-item]").click()
        self.item(self.note).locator("input[data-select-item]").click(
            modifiers=["Shift"]
        )

        for file_obj in (self.folder, self.blob, self.note):
            expect(self.item(file_obj)).to_have_attribute("data-selected", "")
            expect(
                self.item(file_obj).locator("input[data-select-item]")
            ).to_be_checked()
        expect(self.page.locator("#folder-browser")).to_contain_text("3 selected")
        self.assertEqual(self.opened(), [])
        expect(self.page.locator("#folder-browser h1")).to_have_text("My Files")

    def test_a_click_extends_a_selection_under_way_whatever_the_item(self):
        # Opening is off while a selection is being made, so a file that has
        # no viewer is as selectable by a click as any other item.
        self.open_listing()
        self.item(self.note).locator("input[data-select-item]").click()

        self.plain_spot(self.blob).click()
        self.plain_spot(self.folder).click()

        for file_obj in (self.folder, self.blob, self.note):
            expect(self.item(file_obj)).to_have_attribute("data-selected", "")
        self.assertEqual(self.opened(), [])
        expect(self.page.locator("#folder-browser h1")).to_have_text("My Files")

    def test_the_star_button_adds_the_item_to_favorites(self):
        self.open_listing()
        with self.page.expect_response(
            lambda r: (
                r.request.method == "POST"
                and f"/api/v1/files/{self.note.uuid}/favorite" in r.url
            )
        ):
            self.item(self.note).get_by_role("button", name="Add to favorites").click()
        expect(self.item(self.note)).to_have_attribute("data-favorite", "1")
        self.assertEqual(self.opened(), [])

    def test_the_menu_button_and_a_right_click_open_the_item_menu(self):
        self.open_listing()
        self.wait_for_actions(self.note)

        self.item(self.note).locator(
            'button:has([data-lucide="more-vertical"])'
        ).click()
        self.assertEqual(
            self.menu_target(), {"uuid": str(self.note.uuid), "background": False}
        )
        self.close_menu()

        self.plain_spot(self.blob).click(button="right")
        self.assertEqual(
            self.menu_target(), {"uuid": str(self.blob.uuid), "background": False}
        )
        self.assertEqual(self.opened(), [])

    def test_the_hovered_item_is_the_shortcut_target_until_the_pointer_leaves(self):
        self.open_listing()
        self.wait_for_actions(self.note)
        dialog = self.page.locator("#rename-dialog")

        self.plain_spot(self.note).hover()
        self.page.keyboard.press("F2")
        expect(dialog).to_have_attribute("open", "")
        expect(dialog.locator("input")).to_have_value("notes.txt")

        self.page.keyboard.press("Escape")
        expect(dialog).not_to_have_attribute("open", "")
        self.page.locator("#folder-browser h1").hover()
        self.page.keyboard.press("F2")
        self.page.wait_for_timeout(200)
        expect(dialog).not_to_have_attribute("open", "")

    def test_an_item_dims_and_shows_a_spinner_while_an_action_runs_on_it(self):
        self.open_listing()
        item = self.item(self.note)
        star = item.get_by_role("button", name="Add to favorites")
        spinner = item.locator(".loading").first

        self.browser_call("_startLoading", self.note)
        expect(item).to_have_css("opacity", "0.5")
        expect(item).to_have_css("pointer-events", "none")
        expect(spinner).to_be_visible()
        expect(star).to_be_hidden()

        self.browser_call("_stopLoading", self.note)
        expect(item).to_have_css("opacity", "1")
        expect(spinner).to_be_hidden()
        expect(star).to_be_attached()
        expect(self.item(self.blob)).to_have_css("opacity", "1")

    def test_items_are_draggable_once_their_actions_allow_a_cut(self):
        self.open_listing()
        self.wait_for_actions(self.note, self.blob)
        for file_obj in (self.folder, self.blob, self.note):
            expect(self.item(file_obj)).to_have_attribute("draggable", "true")

    def test_trashed_files_open_but_nothing_navigates_or_drags(self):
        for node in (self.note, self.folder):
            node.deleted_at = timezone.now()
            node.save()
        self.open_listing("/files/trash")
        self.wait_for_actions(self.note)

        self.plain_spot(self.folder).click()
        self.page.wait_for_timeout(200)
        self.assertTrue(self.page.url.endswith("/files/trash"))
        expect(self.item(self.folder)).not_to_have_attribute("draggable", "true")

        self.plain_spot(self.note).click(button="right")
        self.assertEqual(
            self.menu_target(), {"uuid": str(self.note.uuid), "background": False}
        )
        self.close_menu()

        # Last: the viewer it opens covers the listing.
        self.plain_spot(self.note).click()
        self.page.wait_for_function("window.__opened.length > 0")
        self.assertEqual(self.opened(), [str(self.note.uuid)])
        expect(self.item(self.note)).not_to_have_attribute("draggable", "true")


class ListItemInteractionTests(_ListingItemInteractions, PlaywrightTestCase):
    VIEW_MODE = "list"


class MosaicItemInteractionTests(_ListingItemInteractions, PlaywrightTestCase):
    VIEW_MODE = "mosaic"
