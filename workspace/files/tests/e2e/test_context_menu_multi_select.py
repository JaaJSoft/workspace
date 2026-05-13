"""E2E regression test: right-click context menu actions act on the
whole selection when the right-clicked file is part of it.

Issue #40: selecting multiple files and right-clicking one of them used
to apply the menu action only to the single file under the cursor. The
fix makes ``openContextMenu`` in ``table.js`` inspect ``selectedUuids``
and, when the right-clicked uuid is part of a multi-selection, replace
``nodeData.actions`` with the bulk-action intersection plus a
``selectionUuids`` payload. The context menu dispatches a
``bulk-action`` event instead of a single-file ``file-action`` event.

This test pins both halves of the behavior:

* Right-clicking one of two selected files and choosing "Move to trash"
  moves BOTH files to trash.
* Right-clicking an unselected file with an existing selection acts
  only on that single file (the selection is replaced, per the issue's
  spec of standard OS behavior).
"""
from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File


class ContextMenuMultiSelectTests(PlaywrightTestCase):

    def _make_file(self, owner, name):
        return File.objects.create(
            owner=owner,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type='text/plain',
        )

    def _select_checkbox(self, uuid):
        # ``data-uuid`` rows render a per-row checkbox under
        # ``td[data-col="select"]``. Click it to toggle selection.
        self.page.locator(
            f'tr[data-uuid="{uuid}"] td[data-col="select"] input[type="checkbox"]'
        ).click()

    def _wait_for_actions_loaded(self):
        # The page boots, then ``fileTableControls.init()`` fires
        # ``POST /api/v1/files/actions`` for every visible uuid. We need
        # ``actionsMap`` to be populated before opening the context menu,
        # otherwise the bulk-action intersection in table.js evaluates
        # against an empty map and the menu renders zero items.
        with self.page.expect_response(
            lambda r: (
                r.request.method == 'POST'
                and '/api/v1/files/actions' in r.url
                and r.ok
            )
        ):
            pass  # Caller already navigated; just wait for the response.

    def test_right_click_on_selected_file_acts_on_whole_selection(self):
        user = self.create_user(username='alice')
        f1 = self._make_file(user, 'one.txt')
        f2 = self._make_file(user, 'two.txt')
        f3 = self._make_file(user, 'three.txt')

        self.login_as(user)

        # Capture the actions response so we know the row-level action
        # cache is populated before we open the context menu - same trick
        # ``test_context_menu_actions.py`` uses.
        with self.page.expect_response(
            lambda r: (
                r.request.method == 'POST'
                and '/api/v1/files/actions' in r.url
            )
        ):
            self.page.goto(f'{self.live_server_url}/files')

        # Visible sanity check - the rows render in list view.
        expect(self.page.locator(f'tr[data-uuid="{f1.uuid}"]')).to_be_visible()
        expect(self.page.locator(f'tr[data-uuid="{f2.uuid}"]')).to_be_visible()
        expect(self.page.locator(f'tr[data-uuid="{f3.uuid}"]')).to_be_visible()

        # Select the first two files via their row checkboxes.
        self._select_checkbox(f1.uuid)
        self._select_checkbox(f2.uuid)

        # Right-click f1 (it is part of the selection). The context menu
        # should target both f1 and f2.
        self.page.locator(f'tr[data-uuid="{f1.uuid}"]').click(button='right')

        menu = self.page.locator('[x-data*="contextMenu"]')
        expect(menu).to_be_visible()

        # Disable the "confirm before delete" dialog so the test doesn't
        # need to drive an AppDialog modal. The pref is stored in the
        # ``files`` user-settings module under ``preferences``.
        self.page.evaluate(
            """() => {
              window._filePrefsCache = window._filePrefsCache || {};
              window._filePrefsCache.confirmBeforeDelete = false;
            }"""
        )

        # Find and click the "Delete" / "Move to trash" entry. The label
        # comes from the action registry - match by the lucide trash icon
        # which is more stable than the exact label text.
        delete_button = menu.locator('button:has(i[data-lucide="trash-2"])').first
        expect(delete_button).to_be_visible()
        delete_button.click()

        # Wait for both DELETE requests to land. The bulk handler in
        # browser.js fires one DELETE per uuid.
        self.page.wait_for_function(
            f"""() => {{
              return !document.querySelector('tr[data-uuid="{f1.uuid}"]')
                  && !document.querySelector('tr[data-uuid="{f2.uuid}"]');
            }}"""
        )

        # Database assertion: f1 and f2 are soft-deleted, f3 untouched.
        f1.refresh_from_db()
        f2.refresh_from_db()
        f3.refresh_from_db()
        self.assertIsNotNone(
            f1.deleted_at,
            "f1 should have been moved to trash by the bulk action",
        )
        self.assertIsNotNone(
            f2.deleted_at,
            "f2 should have been moved to trash by the bulk action - "
            "this is the regression the issue describes",
        )
        self.assertIsNone(
            f3.deleted_at,
            "f3 was not selected and was not right-clicked - it must "
            "not have been deleted",
        )

    def test_right_click_outside_selection_acts_only_on_that_file(self):
        user = self.create_user(username='bob')
        f1 = self._make_file(user, 'one.txt')
        f2 = self._make_file(user, 'two.txt')
        f3 = self._make_file(user, 'three.txt')

        self.login_as(user)

        with self.page.expect_response(
            lambda r: (
                r.request.method == 'POST'
                and '/api/v1/files/actions' in r.url
            )
        ):
            self.page.goto(f'{self.live_server_url}/files')

        # Select f1 and f2, but right-click on f3 (NOT in selection).
        # Per the issue spec, the selection should be replaced by f3 and
        # the menu should target f3 only.
        self._select_checkbox(f1.uuid)
        self._select_checkbox(f2.uuid)

        self.page.locator(f'tr[data-uuid="{f3.uuid}"]').click(button='right')

        menu = self.page.locator('[x-data*="contextMenu"]')
        expect(menu).to_be_visible()

        self.page.evaluate(
            """() => {
              window._filePrefsCache = window._filePrefsCache || {};
              window._filePrefsCache.confirmBeforeDelete = false;
            }"""
        )

        delete_button = menu.locator('button:has(i[data-lucide="trash-2"])').first
        expect(delete_button).to_be_visible()
        delete_button.click()

        # Only f3 should disappear from the listing.
        self.page.wait_for_function(
            f"""() => !document.querySelector('tr[data-uuid="{f3.uuid}"]')"""
        )

        f1.refresh_from_db()
        f2.refresh_from_db()
        f3.refresh_from_db()
        self.assertIsNone(
            f1.deleted_at,
            "f1 was in selection but the right-click was on f3 - the "
            "spec requires the selection to be replaced, so f1 must "
            "survive",
        )
        self.assertIsNone(f2.deleted_at)
        self.assertIsNotNone(
            f3.deleted_at,
            "f3 was the right-click target and should be the only file "
            "moved to trash",
        )
