"""The entry panel and the entry form, driven in a real browser."""

from workspace.vault.tests.e2e.test_browser import PANEL, VaultBrowserCase


class FileEntryBrowserTests(VaultBrowserCase):
    def test_a_note_written_in_the_form_reads_back_in_the_panel(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")

        self.page.click("tbody tr:has-text('GitHub')")
        self.page.locator(PANEL).get_by_role("button", name="Edit").click()
        self.page.wait_for_selector(".modal-box")
        self.page.get_by_role("textbox", name="Notes").fill("first line\nsecond line")
        self.page.click(".modal-box button:has-text('Save')")
        self.page.wait_for_selector(
            ".modal-box:has-text('Save')", state="hidden", timeout=30000
        )

        self.page.click("tbody tr:has-text('GitHub')")
        notes = self.page.locator(PANEL).locator("div.whitespace-pre-wrap")
        notes.wait_for(timeout=15000)
        self.assertEqual(notes.inner_text(), "first line\nsecond line")
