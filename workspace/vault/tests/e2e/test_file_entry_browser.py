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

    def test_the_move_notice_comes_back_after_it_was_dismissed(self):
        """The dismiss button takes the alert out of the DOM, so the next move
        must build it again rather than look for the element it removed."""
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")
        self.page.get_by_role("button", name="New", exact=True).click()
        self.page.get_by_role("button", name="New folder").first.click()
        self.page.fill(".modal-box input[type=text]", "Banking")
        self.page.click(".modal-box button:has-text('Create')")
        self.page.wait_for_selector("tbody tr:has-text('Banking')", timeout=30000)

        # The move dialog has no markup of its own yet, so the component's
        # write path is driven directly - the alert is what is under test.
        move = """(target) => {
            const app = Alpine.$data(document.querySelector('[x-data="vaultBrowser()"]'));
            const entry = app.entries.find((e) => e.name === 'GitHub');
            const folder = app.folders.find((f) => f.name === target);
            return app.moveEntries([entry.uuid], folder ? folder.uuid : null);
        }"""
        notice = self.page.locator("inline-alert[type=success]")

        self.page.evaluate(move, "Banking")
        notice.get_by_text("Moved 1 entry to Banking.").wait_for(timeout=30000)
        notice.get_by_role("button", name="Dismiss").click()
        notice.wait_for(state="detached", timeout=5000)

        self.page.evaluate(move, None)
        notice.get_by_text("Moved 1 entry to").wait_for(timeout=30000)
        self.assertTrue(notice.is_visible())
