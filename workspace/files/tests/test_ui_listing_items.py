"""The attributes the table component reads off a listing item.

table.js selects, sorts, filters and opens an item from the attributes of
its outer element, the same in the list view (a row) and the mosaic view
(a tile): switching the view must not change what the component reads.
"""

from html.parser import HTMLParser

from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.services import trash, tree
from workspace.files.services.marks import set_favorite
from workspace.files.services.shortcuts import create_shortcut
from workspace.users.services.settings import set_setting

from .test_ui_shortcuts import ShortcutBrowserFixtures


class _ItemAttributes(HTMLParser):
    """The attributes of every element whose id names a node."""

    def __init__(self):
        super().__init__()
        self.items = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if (attrs.get("id") or "").startswith("node-"):
            self.items[attrs["id"]] = {
                name: value
                for name, value in attrs.items()
                if name == "draggable" or name.startswith("data-")
            }


def item_attributes(html):
    parser = _ItemAttributes()
    parser.feed(html)
    return parser.items


class ListingItemAttributesTests(ShortcutBrowserFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.inner = tree.create_folder(self.alice, "Inner", parent=self.docs)
        self.notes = tree.create_file(
            self.alice,
            "notes.md",
            parent=self.docs,
            content=ContentFile(b"# notes", name="notes.md"),
            mime_type="text/markdown",
        )
        create_shortcut(self.alice, self.report, self.docs, name="Report link")
        create_shortcut(self.alice, self.team_root, self.docs, name="Team link")
        set_favorite(self.alice, [self.notes.uuid], True)

    def both_views(self, url):
        self.client.force_login(self.alice)
        views = {}
        for mode in ("list", "mosaic"):
            set_setting(self.alice, "files", "preferences", {"defaultViewMode": mode})
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            views[mode] = item_attributes(response.content.decode())
        return views["list"], views["mosaic"]

    def test_a_tile_carries_the_attributes_of_its_row(self):
        rows, tiles = self.both_views(f"/files/{self.docs.uuid}")

        self.assertEqual(len(rows), 4)
        self.assertEqual(tiles, rows)
        folder = rows[f"node-{self.inner.uuid}"]
        self.assertEqual(folder["draggable"], "true")
        self.assertEqual(folder["data-drop-folder"], str(self.inner.uuid))
        self.assertEqual(rows[f"node-{self.notes.uuid}"]["data-favorite"], "1")
        shortcuts = [item for item in rows.values() if "data-opens" in item]
        self.assertEqual(
            sorted(item["data-opens"] for item in shortcuts), ["folder", "viewer"]
        )

    def test_a_trashed_tile_carries_the_attributes_of_its_row(self):
        trash.trash_item(self.alice, self.inner)
        trash.trash_item(self.alice, self.notes)

        rows, tiles = self.both_views("/files/trash")

        self.assertEqual(len(rows), 2)
        self.assertEqual(tiles, rows)
        # A trashed folder is no drop target, and sorts by when it was trashed.
        folder = rows[f"node-{self.inner.uuid}"]
        self.assertNotIn("data-drop-folder", folder)
        self.inner.refresh_from_db()
        self.assertEqual(
            folder["data-updated"], str(int(self.inner.deleted_at.timestamp()))
        )
