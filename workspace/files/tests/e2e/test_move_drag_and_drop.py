"""E2E tests: moving files and folders by dragging them onto a folder.

The gesture is pure HTML5 drag & drop, wired in
``files/ui/static/files/ui/js/drag_move.js``: the row's ``@dragstart``
(``partials/folder_content.html``) hands the dragged items over, and every
element carrying ``data-drop-folder`` - the folders of the listing, the
breadcrumb ancestors, the sidebar entries - is a target served by one set
of listeners on the document. None of it is reachable from a Django test:
the browser owns the ``DataTransfer``, decides whether a drop is allowed
from ``preventDefault()`` during ``dragover``, and hit-tests the target
against the live layout.

What these guard against:

  * the source declaring an ``effectAllowed`` that does not contain the
    ``move`` the targets ask for. The browser then resets ``dropEffect``
    to ``none`` and refuses the drop silently - and Playwright cannot see
    it, since its synthesized drags report ``effectAllowed = 'all'``. Only
    the negotiated values can be asserted, so they are.
  * a target losing its ``data-drop-folder`` hook after a template edit:
    the drop then lands nowhere, without an error.
  * the feedback disappearing (a target that no longer lights up is
    indistinguishable from a broken one), read as computed style rather
    than a class name, so a purged utility fails the test too.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, PinnedFolder


class MoveDragAndDropTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        # Below the ``lg`` breakpoint the sidebar is a 64px rail whose
        # entries are too small to aim a drag at.
        self.page.set_viewport_size({"width": 1280, "height": 800})

    def make_folder(self, name, parent=None):
        return File.objects.create(
            owner=self.user,
            name=name,
            node_type=File.NodeType.FOLDER,
            parent=parent,
        )

    def make_file(self, name, parent=None):
        return File.objects.create(
            owner=self.user,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            parent=parent,
        )

    def open_listing(self, folder=None):
        self.login_as(self.user)
        url = f"{self.live_server_url}/files"
        if folder is not None:
            url = f"{url}/{folder.uuid}"
        self.page.goto(url)
        # A row is only draggable once the action registry has answered:
        # wait for the listing's actions request rather than for a row.
        self.page.wait_for_load_state("networkidle")

    def row(self, node):
        return self.page.locator(f"tr[data-uuid='{node.uuid}']")

    def record_negotiation(self):
        """Capture ``effectAllowed`` at ``dragstart`` and the last
        ``dropEffect`` asked for while over a ``data-drop-folder``."""
        self.page.evaluate(
            """() => {
                window.__negotiated = { effectAllowed: null, dropEffect: null, types: [] };
                window.addEventListener('dragstart', (e) => {
                    window.__negotiated.effectAllowed = e.dataTransfer.effectAllowed;
                    window.__negotiated.types = [...e.dataTransfer.types];
                }, false);
                window.addEventListener('dragover', (e) => {
                    if (e.target.closest && e.target.closest('[data-drop-folder]')) {
                        window.__negotiated.dropEffect = e.dataTransfer.dropEffect;
                    }
                }, false);
            }"""
        )
        return lambda: self.page.evaluate("window.__negotiated")

    def drag(self, source, target, *, mid_drag=None):
        """A real HTML5 drag from ``source`` onto ``target``.

        The target box is captured up front and steered to manually:
        ``drag_to`` re-resolves it mid-drag, and the sidebar re-lays-out
        when its drop hint toggles. ``mid_drag`` runs while the button is
        still down - the only moment the drop feedback is on screen.
        """
        src = source.bounding_box()
        dst = target.bounding_box()
        self.page.mouse.move(src["x"] + src["width"] / 2, src["y"] + src["height"] / 2)
        self.page.mouse.down()
        # A short first move inside the source promotes the gesture to a
        # drag and emits ``dragstart``.
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
        # A target the listing did not already vouch for is asked to the
        # action registry on first hover; let that answer land, then nudge
        # so the browser negotiates the drop against it.
        self.page.wait_for_timeout(400)
        self.page.mouse.move(
            dst["x"] + dst["width"] / 2 + 2,
            dst["y"] + dst["height"] / 2 + 2,
            steps=3,
        )
        if mid_drag is not None:
            mid_drag()
        self.page.mouse.up()

    def test_source_allows_the_move_effect_a_folder_asks_for(self):
        """The folder row asks for ``move``; the source must allow it, and
        also ``copy``, which the pin zone in the sidebar asks for from the
        same drag."""
        folder = self.make_folder("Reports")
        note = self.make_file("notes.txt")
        self.open_listing()
        expect(self.row(note)).to_have_attribute("draggable", "true")

        negotiated = self.record_negotiation()
        self.drag(self.row(note), self.row(folder))

        result = negotiated()
        assert result["effectAllowed"] == "copyMove", (
            f"drag source must allow both the move a folder asks for and "
            f"the copy the pin zone asks for; got {result['effectAllowed']!r}"
        )
        assert "application/x-file-nodes" in result["types"], (
            f"the drag carries no move payload - DataTransfer has {result['types']}"
        )
        assert result["dropEffect"] == "move", (
            f"a folder target should ask for 'move', got {result['dropEffect']!r}"
        )

    def test_dropping_a_file_on_a_folder_row_moves_it_and_shows_feedback(self):
        folder = self.make_folder("Reports")
        note = self.make_file("notes.txt")
        self.open_listing()
        target = self.row(folder)
        expect(target).to_be_visible()

        feedback = {}

        def capture():
            cell = target.locator("td").first
            feedback["target"] = cell.evaluate(
                "el => getComputedStyle(el).backgroundColor"
            )
            feedback["other"] = (
                self.row(note)
                .locator("td")
                .first.evaluate("el => getComputedStyle(el).backgroundColor")
            )
            feedback["source_opacity"] = self.row(note).evaluate(
                "el => getComputedStyle(el).opacity"
            )

        self.drag(self.row(note), target, mid_drag=capture)

        # The listing re-renders without the moved row.
        expect(self.row(note)).to_have_count(0)
        note.refresh_from_db()
        assert note.parent_id == folder.pk, "drop did not move the file"

        assert feedback["target"] != feedback["other"], (
            "the folder under the pointer should be tinted while dragging"
        )
        assert float(feedback["source_opacity"]) < 1, (
            "the dragged row should be dimmed while dragging"
        )

    def test_dropping_on_a_breadcrumb_moves_into_that_ancestor(self):
        folder = self.make_folder("Reports")
        note = self.make_file("notes.txt", parent=folder)
        self.open_listing(folder)
        expect(self.row(note)).to_be_visible()

        root_crumb = self.page.locator("#folder-browser a[data-drop-folder='']")
        expect(root_crumb).to_have_count(1)
        self.drag(self.row(note), root_crumb)

        expect(self.row(note)).to_have_count(0)
        note.refresh_from_db()
        assert note.parent_id is None, "drop on the root crumb did not move the file"

    def test_dragging_a_selected_item_moves_the_whole_selection_onto_a_pinned_folder(
        self,
    ):
        folder = self.make_folder("Reports")
        PinnedFolder.objects.create(owner=self.user, folder=folder, position=0)
        first = self.make_file("a.txt")
        second = self.make_file("b.txt")
        self.open_listing()

        for node in (first, second):
            self.row(node).locator("input[type=checkbox]").click()
        expect(self.page.get_by_text("2 items selected")).to_be_visible()

        pinned = self.page.locator(
            f"li.pinned-folder-item[data-pinned-uuid='{folder.uuid}']"
        )
        hint = self.page.get_by_text("Drop to pin folder")

        def capture():
            # Over a pinned folder the gesture is a move, so the pin zone
            # must not advertise a pin at the same time.
            assert not hint.is_visible(), "the pin hint showed over a move target"

        self.drag(self.row(first), pinned, mid_drag=capture)

        expect(self.row(first)).to_have_count(0)
        expect(self.row(second)).to_have_count(0)
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.parent_id == folder.pk
        assert second.parent_id == folder.pk

    def test_a_folder_dropped_on_itself_is_refused(self):
        folder = self.make_folder("Reports")
        self.open_listing()
        target = self.row(folder)
        expect(target).to_be_visible()

        negotiated = self.record_negotiation()
        feedback = {}

        def capture():
            feedback["class"] = target.get_attribute("class")

        self.drag(target, target, mid_drag=capture)

        result = negotiated()
        assert result["dropEffect"] == "none", (
            f"a folder over itself must be refused, got {result['dropEffect']!r}"
        )
        assert "file-drop-target" not in feedback["class"]
        folder.refresh_from_db()
        assert folder.parent_id is None

    def test_a_folder_the_user_cannot_write_to_is_refused(self):
        """A shared folder the user can only view is not a target: the
        registry's ``paste_into`` decides, not the frontend."""
        from workspace.files.models import FileShare

        other = self.create_user(username="bob")
        readonly = File.objects.create(
            owner=other, name="Bob's", node_type=File.NodeType.FOLDER
        )
        FileShare.objects.create(
            file=readonly,
            shared_by=other,
            shared_with=self.user,
            permission=FileShare.Permission.READ_ONLY,
        )
        PinnedFolder.objects.create(owner=self.user, folder=readonly, position=0)
        note = self.make_file("notes.txt")
        self.open_listing()

        negotiated = self.record_negotiation()
        pinned = self.page.locator(
            f"li.pinned-folder-item[data-pinned-uuid='{readonly.uuid}']"
        )
        self.drag(self.row(note), pinned)

        assert negotiated()["dropEffect"] == "none"
        note.refresh_from_db()
        assert note.parent_id is None, "a view-only folder took the drop"
