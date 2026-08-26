"""The data migration that empties the live tree of already-trashed bytes.

Run against the migration's own function with the current app registry: the
columns are the same either way, so what is worth pinning is where the bytes
end up - including for the rows a live namesake had already taken over.
"""

import importlib
import os
import shutil
import tempfile

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.files.models import File

migration = importlib.import_module(
    "workspace.files.migrations.0044_move_trashed_blobs"
)

User = get_user_model()


class _FakeSchemaEditor:
    class connection:
        alias = "default"


class MoveTrashedBlobsTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self._override = self.settings(MEDIA_ROOT=self.media_root)
        self._override.enable()
        self.user = User.objects.create_user(username="legacy", password="pass")
        self.storage = File._meta.get_field("content").storage

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _write(self, path, data):
        full = os.path.join(self.media_root, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)

    def _row(self, name, *, node_type="file", parent=None, content=None, trashed=False):
        """A row in the pre-migration shape: trashed, blob still in the tree."""
        row = File.objects.create(
            owner=self.user, name=name, node_type=node_type, parent=parent
        )
        updates = {}
        if content is not None:
            updates["content"] = content
        if trashed:
            updates["deleted_at"] = "2026-01-01T00:00:00Z"
        if updates:
            File.objects.filter(pk=row.pk).update(**updates)
        row.refresh_from_db()
        return row

    def _migrate(self):
        migration.move_trashed_blobs(apps, _FakeSchemaEditor())

    def _bytes_of(self, row):
        row.refresh_from_db()
        with row.content.open("rb") as fh:
            return fh.read()

    def test_a_trashed_file_leaves_the_live_tree(self):
        self._write("files/users/legacy/report.pdf", b"TRASHED")
        row = self._row(
            "report.pdf", content="files/users/legacy/report.pdf", trashed=True
        )

        self._migrate()

        row.refresh_from_db()
        self.assertEqual(row.content.name, f"trash/users/legacy/{row.uuid}/report.pdf")
        self.assertEqual(self._bytes_of(row), b"TRASHED")
        self.assertFalse(
            os.path.exists(
                os.path.join(self.media_root, "files/users/legacy/report.pdf")
            )
        )

    def test_a_blob_a_live_row_took_over_is_copied_not_stolen(self):
        # The corruption this migration follows: both rows point at one blob.
        shared = "files/users/legacy/report.pdf"
        self._write(shared, b"THE-LIVE-ONES")
        trashed = self._row("report.pdf", content=shared, trashed=True)
        live = self._row("report.pdf", content=shared)

        self._migrate()

        trashed.refresh_from_db()
        live.refresh_from_db()
        self.assertEqual(live.content.name, shared)
        self.assertNotEqual(trashed.content.name, shared)
        self.assertEqual(self._bytes_of(live), b"THE-LIVE-ONES")
        self.assertEqual(self._bytes_of(trashed), b"THE-LIVE-ONES")

    def test_a_trashed_folder_moves_with_its_subtree(self):
        self._write("files/users/legacy/Docs/deep.txt", b"DEEP")
        folder = self._row("Docs", node_type="folder", trashed=True)
        deep = self._row(
            "deep.txt",
            parent=folder,
            content="files/users/legacy/Docs/deep.txt",
            trashed=True,
        )

        self._migrate()

        deep.refresh_from_db()
        self.assertEqual(
            deep.content.name, f"trash/users/legacy/{folder.uuid}/Docs/deep.txt"
        )
        self.assertEqual(self._bytes_of(deep), b"DEEP")

    def test_only_the_outermost_trashed_node_gets_a_directory(self):
        self._write("files/users/legacy/Docs/deep.txt", b"DEEP")
        folder = self._row("Docs", node_type="folder", trashed=True)
        deep = self._row(
            "deep.txt",
            parent=folder,
            content="files/users/legacy/Docs/deep.txt",
            trashed=True,
        )

        self._migrate()

        deep.refresh_from_db()
        self.assertNotIn(str(deep.uuid), deep.content.name)

    def test_live_rows_are_left_alone(self):
        self._write("files/users/legacy/keep.txt", b"KEEP")
        live = self._row("keep.txt", content="files/users/legacy/keep.txt")

        self._migrate()

        live.refresh_from_db()
        self.assertEqual(live.content.name, "files/users/legacy/keep.txt")
        self.assertEqual(self._bytes_of(live), b"KEEP")

    def test_a_trashed_row_whose_blob_is_gone_is_still_repointed(self):
        row = self._row(
            "ghost.txt", content="files/users/legacy/ghost.txt", trashed=True
        )

        self._migrate()

        row.refresh_from_db()
        self.assertEqual(row.content.name, f"trash/users/legacy/{row.uuid}/ghost.txt")

    def test_running_it_twice_changes_nothing(self):
        self._write("files/users/legacy/report.pdf", b"TRASHED")
        row = self._row(
            "report.pdf", content="files/users/legacy/report.pdf", trashed=True
        )

        self._migrate()
        first = File.objects.get(pk=row.pk).content.name
        self._migrate()

        row.refresh_from_db()
        self.assertEqual(row.content.name, first)
        self.assertEqual(self._bytes_of(row), b"TRASHED")
