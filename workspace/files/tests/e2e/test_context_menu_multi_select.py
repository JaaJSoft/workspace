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

    def _wait_for_actions_loaded(self, *uuids):
        # ``fileTableControls.fetchActions`` writes the API payload onto
        # the component's ``actionsMap`` reactive prop. The HTTP response
        # arrives before the JS assignment completes, so waiting on the
        # network response alone leaves a small race where the right-click
        # fires against an empty actions map. Poll the DOM until we see
        # the rows AND Alpine has the action data for them.
        self.page.wait_for_function(
            """(uuids) => {
              const root = document.querySelector('[x-data*="fileTableWithView"]');
              if (!root || typeof Alpine === 'undefined') return false;
              const data = Alpine.$data(root);
              if (!data || !data.actionsMap) return false;
              return uuids.every(u => Array.isArray(data.actionsMap[u])
                                       && data.actionsMap[u].length > 0);
            }""",
            arg=list(uuids),
        )

    def _delete_button(self, menu):
        # Lucide replaces ``<i data-lucide="...">`` with an ``<svg>``
        # at runtime, so a tag-scoped selector like ``i[data-lucide]``
        # stops matching once Lucide has run. The button itself wraps
        # an icon, the label span, AND a ``<kbd>`` shortcut badge ("Del"),
        # so its full ``innerText`` is "Delete\nDel" - we'd need to use
        # the inner span to match the bare label and avoid colliding
        # with the Purge action ("Delete permanently").
        return menu.locator(
            'button:has(span:text-is("Delete"))'
        ).first

    def test_right_click_on_selected_file_acts_on_whole_selection(self):
        user = self.create_user(username='alice')
        f1 = self._make_file(user, 'one.txt')
        f2 = self._make_file(user, 'two.txt')
        f3 = self._make_file(user, 'three.txt')

        self.login_as(user)

        # Capture the actions response so we know the row-level action
        # cache is populated before we open the context menu - same trick
        # ``test_context_menu_actions.py`` uses.
        self.page.goto(f'{self.live_server_url}/files')

        # Visible sanity check - the rows render in list view.
        expect(self.page.locator(f'tr[data-uuid="{f1.uuid}"]')).to_be_visible()
        expect(self.page.locator(f'tr[data-uuid="{f2.uuid}"]')).to_be_visible()
        expect(self.page.locator(f'tr[data-uuid="{f3.uuid}"]')).to_be_visible()

        self._wait_for_actions_loaded(str(f1.uuid), str(f2.uuid), str(f3.uuid))

        # Disable the "confirm before delete" dialog so the test doesn't
        # need to drive an AppDialog modal. Set this BEFORE the click so
        # ``bulkDeleteItems`` sees ``confirmBeforeDelete = false``.
        self.page.evaluate(
            """() => {
              window._filePrefsCache = window._filePrefsCache || {};
              window._filePrefsCache.confirmBeforeDelete = false;
            }"""
        )

        # Select the first two files via their row checkboxes.
        self._select_checkbox(f1.uuid)
        self._select_checkbox(f2.uuid)

        # Right-click f1 (it is part of the selection). The context menu
        # should target both f1 and f2.
        self.page.locator(f'tr[data-uuid="{f1.uuid}"]').click(button='right')

        menu = self.page.locator('[x-data*="contextMenu"]')
        expect(menu).to_be_visible()

        delete_button = self._delete_button(menu)
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

        self.page.goto(f'{self.live_server_url}/files')

        expect(self.page.locator(f'tr[data-uuid="{f3.uuid}"]')).to_be_visible()
        self._wait_for_actions_loaded(str(f1.uuid), str(f2.uuid), str(f3.uuid))

        self.page.evaluate(
            """() => {
              window._filePrefsCache = window._filePrefsCache || {};
              window._filePrefsCache.confirmBeforeDelete = false;
            }"""
        )

        # Select f1 and f2, but right-click on f3 (NOT in selection).
        # Per the issue spec, the selection should be replaced by f3 and
        # the menu should target f3 only.
        self._select_checkbox(f1.uuid)
        self._select_checkbox(f2.uuid)

        self.page.locator(f'tr[data-uuid="{f3.uuid}"]').click(button='right')

        menu = self.page.locator('[x-data*="contextMenu"]')
        expect(menu).to_be_visible()

        delete_button = self._delete_button(menu)
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
