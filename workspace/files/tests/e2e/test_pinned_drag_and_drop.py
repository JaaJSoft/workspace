"""E2E tests: pinning a folder by dragging it into the sidebar, and
reordering the pinned list by dragging one entry over another.

Both flows are pure HTML5 drag & drop, wired in
``files/ui/static/files/ui/js/pinned.js`` and driven by attributes on
the row (``@dragstart`` in ``partials/folder_content.html``) and on the
sidebar drop zone / list items (``partials/pinned_folders.html``). None
of it is reachable from a Django test: the browser owns the
``DataTransfer`` object, decides whether a drop is allowed based on
``preventDefault()`` during ``dragover``, and resolves the drop target
by hit-testing the live layout.

The bug classes these guard against:

  * a ``dragover`` handler that stops calling ``preventDefault()`` —
    the browser then refuses the drop and the ``drop`` event never
    fires at all, silently. Nothing in the JS errors out.
  * the custom ``application/x-pin-folder`` MIME type drifting apart
    between the ``setData`` on the row and the ``getData`` on the
    drop zone.
  * losing the drag affordances, which is what shipped: the pinned
    ``<li>`` carried two ``:class`` attributes, the HTML parser kept
    only the first, and Alpine never saw the binding driving the
    dragged-item fade and the drop-target ring. Reordering still
    worked, but with zero feedback — indistinguishable from broken.

The feedback assertions deliberately read *computed style*
(``opacity``, ``box-shadow``) rather than class strings: a class name
in ``class=""`` proves nothing about what the user sees, and would
still pass if the utility were purged from the CSS bundle or overridden.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, PinnedFolder


class PinnedFoldersDragAndDropTests(PlaywrightTestCase):
    """Drag a folder into the sidebar to pin it; drag pins to reorder."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        # ``sidebarCollapse()`` force-collapses below the ``lg``
        # breakpoint (1024 px), and a collapsed sidebar hides both the
        # empty-state placeholder and the drop hint — the drop zone
        # then has zero height and nothing can be dropped on it.
        self.page.set_viewport_size({"width": 1280, "height": 800})

    def make_folder(self, name):
        return File.objects.create(
            owner=self.user,
            name=name,
            node_type=File.NodeType.FOLDER,
        )

    def drag(self, source, target, *, mid_drag=None):
        """Perform a real HTML5 drag from ``source`` onto ``target``.

        Playwright's ``drag_to`` re-resolves the target box after the
        drag starts, which is wrong here: the drop zone re-lays-out the
        moment ``dragOver`` flips (the empty-state ``<li>`` is replaced
        by the taller drop hint). We capture the target box up front and
        steer to it manually.

        ``mid_drag`` is invoked while the button is still down, i.e. the
        only moment the drag-feedback styles are applied.
        """
        src = source.bounding_box()
        dst = target.bounding_box()

        self.page.mouse.move(src["x"] + src["width"] / 2, src["y"] + src["height"] / 2)
        self.page.mouse.down()
        # A short first move inside the source is what makes Chromium
        # promote the gesture to a drag and emit ``dragstart``.
        self.page.mouse.move(
            src["x"] + src["width"] / 2,
            src["y"] + src["height"] / 2 + 8,
            steps=5,
        )
        self.page.mouse.move(
            dst["x"] + dst["width"] / 2,
            dst["y"] + dst["height"] / 2,
            steps=20,
        )
        # Let the dragenter-driven re-render settle, then nudge so the
        # browser re-hit-tests against the new layout.
        self.page.wait_for_timeout(300)
        self.page.mouse.move(
            dst["x"] + dst["width"] / 2,
            dst["y"] + dst["height"] / 2 + 2,
            steps=3,
        )
        if mid_drag is not None:
            mid_drag()
        self.page.mouse.up()

    def test_dragging_a_folder_row_pins_it(self):
        folder = self.make_folder("Reports")
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files")

        row = self.page.locator(f"tr[data-uuid='{folder.uuid}']")
        expect(row).to_be_visible()
        # Nothing pinned yet: the list shows only its empty-state entry.
        expect(self.page.locator("li.pinned-folder-item")).to_have_count(0)

        drop_zone = self.page.locator("#pinned-folders-section > div").last
        self.drag(row, drop_zone)

        pinned_item = self.page.locator(
            f"li.pinned-folder-item[data-pinned-uuid='{folder.uuid}']"
        )
        expect(pinned_item).to_be_visible()
        assert PinnedFolder.objects.filter(owner=self.user, folder=folder).exists(), (
            "drop did not persist a PinnedFolder row"
        )

    def test_reordering_pinned_folders_shows_feedback_and_persists(self):
        first = self.make_folder("Archive")
        second = self.make_folder("Reports")
        PinnedFolder.objects.create(owner=self.user, folder=first, position=0)
        PinnedFolder.objects.create(owner=self.user, folder=second, position=1)

        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files")

        items = self.page.locator("li.pinned-folder-item")
        expect(items).to_have_count(2)
        assert items.nth(0).get_attribute("data-pinned-uuid") == str(first.uuid)

        feedback = {}

        def capture():
            feedback["dragged_opacity"] = items.nth(0).evaluate(
                "el => getComputedStyle(el).opacity"
            )
            feedback["target_shadow"] = items.nth(1).evaluate(
                "el => getComputedStyle(el).boxShadow"
            )

        self.drag(items.nth(0), items.nth(1), mid_drag=capture)

        # The dragged entry fades and the hovered entry gets a ring —
        # the two affordances that tell the user the gesture is live.
        assert feedback["dragged_opacity"] == "0.5", (
            f"dragged item should fade to 0.5 opacity while dragging, "
            f"got {feedback['dragged_opacity']!r}"
        )
        assert feedback["target_shadow"] not in {"none", ""}, (
            f"drop target should render a ring while hovered, "
            f"got box-shadow {feedback['target_shadow']!r}"
        )

        # And the reorder actually lands, both in the DOM and server-side.
        expect(items.nth(0)).to_have_attribute("data-pinned-uuid", str(second.uuid))
        self.page.wait_for_timeout(500)
        order = list(
            PinnedFolder.objects.filter(owner=self.user)
            .order_by("position")
            .values_list("folder__name", flat=True)
        )
        assert order == ["Reports", "Archive"], f"unexpected saved order: {order}"
