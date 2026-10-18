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
        self._create_folder("Banking")
        notice = self.page.locator("inline-alert[type=success]")

        self._move_through_the_dialog("GitHub", "Banking")
        notice.get_by_text("Moved 1 entry to Banking.").wait_for(timeout=30000)
        notice.get_by_role("button", name="Dismiss").click()
        notice.wait_for(state="detached", timeout=5000)

        self.page.click("tbody tr:has-text('Banking')")
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", timeout=30000)
        self._move_through_the_dialog("GitHub", "no folder")
        notice.get_by_text("Moved 1 entry to").wait_for(timeout=30000)
        self.assertTrue(notice.is_visible())

    def _move_through_the_dialog(self, entry, place):
        for box in self.page.locator("tbody tr input.checkbox:checked").all():
            box.uncheck()
        self.page.locator(f"tbody tr:has-text('{entry}') input.checkbox").check()
        self.page.get_by_role("button", name="Move to folder").click()
        dialog = self.page.locator("[role=dialog]")
        dialog.wait_for()
        dialog.locator(f"a:has-text('{place}')").click()
        dialog.get_by_role("button", name="Move", exact=True).click()
        dialog.wait_for(state="detached", timeout=30000)

    def _create_folder(self, name):
        self.page.get_by_role("button", name="New", exact=True).click()
        self.page.get_by_role("button", name="New folder").first.click()
        self.page.fill(".modal-box input[type=text]", name)
        self.page.click(".modal-box button:has-text('Create')")
        self.page.wait_for_selector(f"tbody tr:has-text('{name}')", timeout=30000)

    def _open_move_dialog_for_two_entries(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")
        self._create_entry("GitLab", "tanuki", "hunter3")
        self._create_folder("Banking")
        for name in ("GitHub", "GitLab"):
            self.page.locator(f"tbody tr:has-text('{name}') input.checkbox").check()
        self.page.get_by_role("button", name="Move to folder").click()
        self.page.locator("[role=dialog]").wait_for()

    def test_entries_move_to_a_folder_from_the_selection_bar(self):
        self._open_move_dialog_for_two_entries()
        dialog = self.page.locator("[role=dialog]")
        self.assertIn("Move 2 entries", dialog.inner_text())
        move = dialog.get_by_role("button", name="Move", exact=True)
        self.assertTrue(move.is_disabled())

        dialog.locator("a:has-text('Banking')").click()
        self.assertTrue(move.is_enabled())
        move.click()

        self.page.get_by_text("Moved 2 entries to Banking.").wait_for(timeout=30000)
        self.page.locator("[role=dialog]").wait_for(state="detached")
        self.page.click("tbody tr:has-text('Banking')")
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", timeout=30000)
        self.assertTrue(self.page.locator("tbody tr:has-text('GitLab')").is_visible())

    def test_the_move_dialog_closes_on_escape(self):
        self._open_move_dialog_for_two_entries()
        self.page.keyboard.press("Escape")
        self.page.locator("[role=dialog]").wait_for(state="detached", timeout=5000)

    def _create_tag(self, name):
        self.page.get_by_role("button", name="New tag").click()
        self.page.fill(".modal-box input[type=text]", name)
        self.page.click(".modal-box button:has-text('Create')")
        self.page.wait_for_selector(
            f"aside button[aria-pressed]:has-text('{name}')", timeout=30000
        )

    def _open_tags_dialog(self, *names):
        for box in self.page.locator("tbody tr input.checkbox:checked").all():
            box.uncheck()
        for name in names:
            self.page.locator(f"tbody tr:has-text('{name}') input.checkbox").check()
        self.page.get_by_role("button", name="Edit tags").click()
        self.page.locator("[role=dialog]").wait_for()

    def _apply_tags(self, ticks):
        dialog = self.page.locator("[role=dialog]")
        for tag in ticks:
            dialog.locator(f"label:has-text('{tag}') input").click()
        dialog.get_by_role("button", name="Apply").click()
        dialog.wait_for(state="detached", timeout=30000)

    def _row_tags(self, name):
        chips = self.page.locator(f"tbody tr:has-text('{name}') tag-chip")
        return sorted(chip.inner_text().strip() for chip in chips.all())

    def _two_entries_sharing_a_tag(self):
        """A carries X and Y, B carries X: Y is the mixed one."""
        self._open_vault()
        for tag in ("X", "Y", "Z"):
            self._create_tag(tag)
        self._create_entry("Alpha", "a", "pw-a")
        self._create_entry("Beta", "b", "pw-b")
        self._open_tags_dialog("Alpha")
        self._apply_tags(["X", "Y"])
        self._wait_for_chips("Alpha", 2)
        self._open_tags_dialog("Beta")
        self._apply_tags(["X"])
        self._wait_for_chips("Beta", 1)

    def _wait_for_chips(self, name, count):
        self.page.wait_for_function(
            "([name, count]) => [...document.querySelectorAll('tbody tr')]"
            ".some((r) => r.innerText.includes(name)"
            " && r.querySelectorAll('tag-chip').length === count)",
            arg=[name, count],
            timeout=30000,
        )

    def test_bulk_tagging_keeps_a_tag_one_entry_has_alone(self):
        self._two_entries_sharing_a_tag()
        self._open_tags_dialog("Alpha", "Beta")
        self._apply_tags(["Z"])

        self._wait_for_chips("Alpha", 3)
        self.assertEqual(self._row_tags("Alpha"), ["X", "Y", "Z"])
        self.assertEqual(self._row_tags("Beta"), ["X", "Z"])

    def test_the_dash_survives_a_click_cycle(self):
        self._two_entries_sharing_a_tag()
        self._open_tags_dialog("Alpha", "Beta")
        box = self.page.locator("[role=dialog] label:has-text('Y') input")
        read = "(el) => [el.checked, el.indeterminate]"

        self.assertEqual(box.evaluate(read), [False, True])
        box.click()
        self.assertEqual(box.evaluate(read), [True, False])
        box.click()
        self.assertEqual(box.evaluate(read), [False, False])
        box.click()
        self.assertEqual(box.evaluate(read), [False, True])

    def _record_drag(self):
        """Log what the two sides of a drag negotiate, in the real browser.

        Playwright's synthesized drags report ``effectAllowed = 'all'``, which
        contains every effect, so a drop landing proves nothing about a drop
        the browser would refuse. The values are read at ``dragstart`` and at
        ``dragover``, after the component's own handlers have run.
        """
        self.page.evaluate(
            """() => {
                window.__drag = { start: null, over: [] };
                window.addEventListener('dragstart', (e) => {
                    window.__drag.start = {
                        effectAllowed: e.dataTransfer.effectAllowed,
                        types: [...e.dataTransfer.types],
                    };
                });
                window.addEventListener('dragover', (e) => {
                    window.__drag.over.push({
                        dropEffect: e.dataTransfer.dropEffect,
                        prevented: e.defaultPrevented,
                    });
                });
            }"""
        )

    def _drag(self, source, target):
        src = source.bounding_box()
        dst = target.bounding_box()
        self.page.mouse.move(src["x"] + 60, src["y"] + src["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(src["x"] + 60, src["y"] + src["height"] / 2 + 8, steps=5)
        self.page.mouse.move(
            dst["x"] + dst["width"] / 2, dst["y"] + dst["height"] / 2, steps=20
        )
        self.page.wait_for_timeout(300)
        self.page.mouse.move(
            dst["x"] + dst["width"] / 2, dst["y"] + dst["height"] / 2 + 2, steps=3
        )
        self.page.mouse.up()

    def test_dragging_entries_onto_a_folder_moves_them(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")
        self._create_entry("GitLab", "tanuki", "hunter3")
        self._create_folder("Banking")
        self._record_drag()

        self._drag(
            self.page.locator("tbody tr:has-text('GitHub')"),
            self.page.locator("tbody tr:has-text('Banking')"),
        )

        self.page.get_by_text("Moved 1 entry to Banking.").wait_for(timeout=30000)
        drag = self.page.evaluate("window.__drag")
        self.assertEqual(drag["start"]["effectAllowed"], "move")
        self.assertIn("application/x-vault-entries", drag["start"]["types"])
        self.assertIn({"dropEffect": "move", "prevented": True}, drag["over"])
        self.assertTrue(self.page.locator("tbody tr:has-text('GitLab')").is_visible())
        self.assertEqual(self.page.locator("tbody tr:has-text('GitHub')").count(), 0)

        self.page.click("tbody tr:has-text('Banking')")
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", timeout=30000)

    def test_dropping_on_a_breadcrumb_ancestor_moves_up(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")
        self._create_folder("Banking")
        self._drag(
            self.page.locator("tbody tr:has-text('GitHub')"),
            self.page.locator("tbody tr:has-text('Banking')"),
        )
        self.page.get_by_text("Moved 1 entry to Banking.").wait_for(timeout=30000)
        self.page.click("tbody tr:has-text('Banking')")
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", timeout=30000)
        self._record_drag()

        root = self.page.locator("nav button").first
        self._drag(self.page.locator("tbody tr:has-text('GitHub')"), root)

        self.page.wait_for_selector(
            "tbody tr:has-text('GitHub')", state="detached", timeout=30000
        )
        drag = self.page.evaluate("window.__drag")
        self.assertEqual(drag["start"]["effectAllowed"], "move")
        self.assertIn({"dropEffect": "move", "prevented": True}, drag["over"])
        root.click()
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", timeout=30000)

    def test_rows_are_not_draggable_in_the_favourites_view(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")
        self.assertEqual(
            self.page.locator("tbody tr:has-text('GitHub')").get_attribute("draggable"),
            "true",
        )
        self.page.locator(".drawer-side").get_by_role(
            "button", name="Favorites"
        ).click()
        self.page.evaluate(
            """() => Alpine.$data(document.querySelector('[x-data="vaultBrowser()"]'))
                .entries.forEach((e) => { e.favorite = true; })"""
        )
        row = self.page.locator("tbody tr:has-text('GitHub')")
        row.wait_for(timeout=10000)
        self.assertIsNone(row.get_attribute("draggable"))
