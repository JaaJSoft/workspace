"""E2E: the file picker's icons come from the API, hydrated inside x-for.

The picker renders its rows client-side and binds one ``:data-lucide`` to
the ``type_icon`` the API resolves from the file type registry. That
binding lands after the row is cloned out of the ``x-for`` template, and
lucide only draws it because ``observeLucideIcons`` rehydrates a
``data-lucide`` value that changes in place. A Django test can check the
JSON; whether an svg with the right name and color ends up in the row is
the browser's business.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

from ._share_fixtures import build_shared_tree


class FilePickerIconTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        build_shared_tree(self, username="picker-owner")
        self.login_as(self.owner)

    def test_rows_carry_the_registry_icon_and_color(self):
        self.page.goto(f"{self.live_server_url}/")
        self.page.evaluate(
            "document.querySelectorAll('dialog[open]').forEach(d => d.close())"
        )
        # The picker resolves only when the visitor picks or cancels; the
        # page must not await it.
        self.page.evaluate("void AppDialog.filePicker({ multiple: true })")

        dialog = self.page.locator("dialog[open]").last
        # The picker opens at the root, which only holds the Docs folder.
        dialog.get_by_role("button", name="Docs").click()
        expect(dialog.get_by_text("data.csv")).to_be_visible()

        # Scoped to the row: the space chip above the list carries a folder
        # icon of its own.
        csv_row = dialog.get_by_role("button", name="data.csv")
        csv_icon = csv_row.locator('svg[data-lucide="file-spreadsheet"]')
        expect(csv_icon).to_have_count(1)
        expect(csv_icon).to_have_class(re.compile(r"\btext-info\b"))

        sub_row = dialog.get_by_role("button", name="Sub")
        folder_icon = sub_row.locator('svg[data-lucide="folder"]')
        expect(folder_icon).to_have_count(1)
        expect(folder_icon).to_have_class(re.compile(r"\btext-warning\b"))
