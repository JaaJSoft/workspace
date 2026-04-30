"""E2E test: the file context menu renders exactly the actions the
``POST /api/v1/files/actions`` endpoint declares for the target file.

CLAUDE.md > File Actions makes the rule explicit: the backend
``ActionRegistry`` is the single source of truth, and the UI must never
hard-code its own list. Backend tests cover the endpoint contract in
isolation; what only a real browser can prove is that the rendered
menu actually reflects that contract — i.e., that no one re-introduced
a hard-coded ``<li>`` in the template, and that no CSS rule or
``x-show`` clause silently filters items.

The bug class this guards against:

  * a static menu item shipped without an ``is_available`` entry in
    ``workspace/files/actions/`` — would appear in the UI but the
    backend would 403 on click,
  * an ``is_available`` rule that the UI ignores — backend would
    refuse the action but the user sees the option,
  * a refactor that flips between Alpine ``x-for`` and a ``{% for %}``
    template loop and accidentally bakes in stale defaults.
"""
from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File


class ContextMenuMatchesActionsEndpointTests(PlaywrightTestCase):
    """The right-click menu and the actions endpoint return the same set."""

    def test_menu_items_match_api_actions(self):
        user = self.create_user(username="alice")

        # A plain file at the user's root — ``parent=None`` is rendered
        # by ``/files`` as a top-level entry, so we don't need to navigate
        # into a subfolder. Using a file (not folder) keeps the test
        # immune to the ``paste_into`` action's clipboard-gated x-show.
        f = File.objects.create(
            owner=user,
            name="test.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/files")

        # Wait until the row exists and the in-page ``fetchActions()``
        # call (fired in ``fileBrowser().init()``) has populated
        # ``actionsMap`` for our file. Without this wait the right-click
        # races with the API request and the menu opens with an empty
        # ``actions: []`` array.
        row = self.page.locator(f'tr[data-uuid="{f.uuid}"]')
        expect(row).to_be_visible()
        self.page.wait_for_function(
            "(uuid) => {"
            "  const root = document.querySelector('[x-data=\"fileBrowser()\"]');"
            "  if (!root) return false;"
            "  const map = Alpine.evaluate(root, 'actionsMap');"
            "  return Array.isArray(map?.[uuid]) && map[uuid].length > 0;"
            "}",
            arg=str(f.uuid),
        )

        # Independent source of truth: hit the same endpoint the page
        # uses, with the same cookies, via the browser's own fetch. This
        # gives us the JSON the UI is *supposed* to mirror.
        api_response = self.page.evaluate(
            """
            async (uuid) => {
              const resp = await fetch('/api/v1/files/actions', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify({ uuids: [uuid] }),
              });
              return { status: resp.status, body: await resp.json() };
            }
            """,
            arg=str(f.uuid),
        )
        assert api_response["status"] == 200, (
            f"actions endpoint returned {api_response['status']}: "
            f"{api_response['body']!r}"
        )
        api_actions = api_response["body"].get(str(f.uuid), [])
        assert api_actions, (
            f"endpoint returned no actions for {f.uuid}; "
            f"the rest of this test would compare empty sets"
        )
        # ``paste_into`` is folder-only at the backend, so it's already
        # absent for our file; we don't need to filter the API list.
        # We assert this property explicitly so a future change that
        # flips paste_into to also apply to files would surface here.
        api_action_ids = {a["id"] for a in api_actions}
        assert "paste_into" not in api_action_ids, (
            "paste_into now appears for a file — the UI gates its "
            "visibility on hasClipboardItems via x-show, so this test "
            "would need to filter it. Update the test."
        )
        api_labels = sorted(a["label"] for a in api_actions)

        # Right-click the row — fires ``@contextmenu="openContextMenu(...)"``
        # which dispatches ``open-context-menu`` to the menu component.
        row.click(button="right")

        menu = self.page.locator('[x-data="contextMenu()"]')
        expect(menu).to_be_visible()

        # Read the visible label of each ``<li>`` in the menu.
        # Each menu item has three sibling branches gated by ``x-show``
        # (the ``<a>`` for download, the ``<a>`` for open_new_tab, and
        # the default ``<button>``); only one is visible per ``<li>``.
        # We read the visible one's ``<span>`` text — that's the action
        # label as shown to the user.
        menu_labels = self.page.evaluate(
            """
            () => {
              const root = document.querySelector('[x-data="contextMenu()"]');
              if (!root) return [];
              const labels = [];
              for (const li of root.querySelectorAll('li')) {
                if (li.offsetParent === null) continue;  // x-show hidden
                const visible = [...li.querySelectorAll('a, button')]
                  .find(el => el.offsetParent !== null);
                if (!visible) continue;
                const span = visible.querySelector('span');
                if (span && span.textContent.trim()) {
                  labels.push(span.textContent.trim());
                }
              }
              return labels;
            }
            """
        )

        self.assertEqual(
            sorted(menu_labels),
            api_labels,
            "context menu labels diverge from /api/v1/files/actions:\n"
            f"  menu: {sorted(menu_labels)}\n"
            f"  api:  {api_labels}",
        )
