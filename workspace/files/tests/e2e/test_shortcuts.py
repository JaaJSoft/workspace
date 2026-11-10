"""E2E cover for shortcuts in the file browser.

A shortcut opens what it points at - the target folder's listing, or the
target file in the viewer, by the target's own uuid - from a click, from
Enter and from its menu, and a shortcut whose target is gone opens nothing. The menu of a file creates one wherever the folder picker says.
"""

from __future__ import annotations

from django.core.cache import cache
from django.core.files.base import ContentFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File
from workspace.files.services import trash, tree
from workspace.files.services.shortcuts import create_shortcut


class ShortcutBrowserTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.reports = tree.create_folder(self.user, "Reports")
        self.notes = tree.create_file(
            self.user,
            "notes.txt",
            parent=self.reports,
            content=ContentFile(b"minutes", name="notes.txt"),
            mime_type="text/plain",
        )
        self.links = tree.create_folder(self.user, "Links")
        self.folder_shortcut = create_shortcut(
            self.user, self.reports, None, name="Reports link"
        )
        self.file_shortcut = create_shortcut(
            self.user, self.notes, None, name="Notes link"
        )

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def row(self, file_obj):
        return self.page.locator(f'tr[data-uuid="{file_obj.uuid}"]')

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

    def test_a_click_on_a_folder_shortcut_opens_the_target_folder(self):
        self.open_listing()

        self.row(self.folder_shortcut).locator('td[data-col="size"]').click()

        expect(self.page.locator("#folder-browser h1")).to_have_text("Reports")
        self.assertIn(str(self.reports.uuid), self.page.url)

    def test_a_click_on_a_file_shortcut_views_the_target(self):
        self.open_listing()

        self.row(self.file_shortcut).locator('td[data-col="size"]').click()

        self.page.wait_for_function("window.__opened.length > 0")
        self.assertEqual(self.opened(), [str(self.notes.uuid)])

    def test_enter_opens_the_target(self):
        self.open_listing()
        self.wait_for_actions(self.file_shortcut)

        self.row(self.file_shortcut).hover()
        self.page.keyboard.press("Enter")

        self.page.wait_for_function("window.__opened.length > 0")
        self.assertEqual(self.opened(), [str(self.notes.uuid)])

    def test_the_menu_opens_the_target(self):
        self.open_listing()
        self.wait_for_actions(self.folder_shortcut)

        self.row(self.folder_shortcut).click(button="right")
        self.page.locator("ul.menu span:visible", has_text="Open").first.click()

        expect(self.page.locator("#folder-browser h1")).to_have_text("Reports")

    def test_a_shortcut_whose_target_is_gone_opens_nothing(self):
        trash.soft_delete(self.notes, acting_user=self.user)
        self.open_listing()

        self.row(self.file_shortcut).locator('td[data-col="size"]').click()

        self.page.wait_for_timeout(300)
        self.assertEqual(self.opened(), [])
        expect(self.page.locator("#folder-browser h1")).to_have_text("My Files")

    def test_the_menu_files_a_shortcut_where_the_picker_says(self):
        self.open_listing(f"/files/{self.reports.uuid}")
        self.wait_for_actions(self.notes)

        self.row(self.notes).click(button="right")
        self.page.locator("ul.menu span:visible", has_text="Create shortcut").click()
        picker = self.page.locator("#app-dialog-picker")
        picker.get_by_text("Links", exact=True).click()
        with self.page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/api/v1/files")
        ) as response:
            picker.get_by_role("button", name="Create").click()

        self.assertEqual(response.value.status, 201)
        shortcut = File.objects.get(parent=self.links)
        self.assertEqual(shortcut.node_type, File.NodeType.SHORTCUT)
        self.assertEqual(shortcut.shortcut_target_id, self.notes.pk)
        self.assertEqual(shortcut.name, "notes.txt")
