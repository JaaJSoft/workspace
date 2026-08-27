from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase

from workspace.common.search import fts5_available
from workspace.files.models import File, FileEvent
from workspace.files.services.search_index import (
    FILES_FTS,
    build_document,
    index_file,
    unindex_file,
)

User = get_user_model()


class BuildDocumentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")

    def test_folder_document_is_name_only(self):
        folder = File.objects.create(
            name="Recipes", node_type=File.NodeType.FOLDER, owner=self.user
        )
        self.assertEqual(build_document(folder), {"name": "Recipes", "body": ""})

    def test_note_document_carries_the_body(self):
        note = File.objects.create(
            name="note.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            owner=self.user,
            content=ContentFile(b"the kraken sleeps", name="note.md"),
        )
        doc = build_document(note)
        self.assertEqual(doc["name"], "note.md")
        self.assertIn("kraken", doc["body"])

    def test_binary_document_is_name_only(self):
        img = File.objects.create(
            name="photo.png",
            node_type=File.NodeType.FILE,
            mime_type="image/png",
            owner=self.user,
            content=ContentFile(b"\x89PNG\xff\xfe", name="photo.png"),
        )
        self.assertEqual(build_document(img)["body"], "")


class _FtsTestCase(TestCase):
    """Runs against the real FTS table created by the migration."""

    def setUp(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        self.user = User.objects.create_user(username="alice", password="pw")

    def _note(self, name, body):
        return File.objects.create(
            name=name,
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            owner=self.user,
            content=ContentFile(body, name=name),
        )

    def _matches(self, match):
        with connection.cursor() as c:
            c.execute(
                f"SELECT rowid FROM {FILES_FTS.fts_table} "
                f"WHERE {FILES_FTS.fts_table} MATCH %s",
                [match],
            )
            return {row[0] for row in c.fetchall()}

    def _rowid(self, file_obj):
        with connection.cursor() as c:
            c.execute("SELECT rowid FROM files_file WHERE uuid = %s", [file_obj.pk.hex])
            return c.fetchone()[0]


class IndexWriteTests(_FtsTestCase):
    def test_indexing_makes_the_body_searchable(self):
        note = self._note("groceries.md", b"buy some saffron")
        index_file(note)
        self.assertEqual(self._matches('"saffron"'), {self._rowid(note)})

    def test_reindexing_reflects_an_edit(self):
        note = self._note("groceries.md", b"buy some saffron")
        index_file(note)
        note.content.save("groceries.md", ContentFile(b"buy some paprika"), save=True)
        index_file(note)
        self.assertEqual(self._matches('"saffron"'), set())
        self.assertEqual(self._matches('"paprika"'), {self._rowid(note)})

    def test_unindexing_removes_the_document(self):
        note = self._note("groceries.md", b"buy some saffron")
        index_file(note)
        unindex_file(note)
        self.assertEqual(self._matches('"saffron"'), set())

    def test_trashing_keeps_the_document(self):
        # The row survives a soft delete and the access querysets already hide
        # it, so re-indexing on trash would be wasted work.
        note = self._note("groceries.md", b"buy some saffron")
        index_file(note)
        note.delete()
        self.assertEqual(self._matches('"saffron"'), {self._rowid(note)})

    def test_hard_delete_removes_the_document(self):
        note = self._note("groceries.md", b"buy some saffron")
        index_file(note)
        rowid = self._rowid(note)
        note.delete(hard=True)
        self.assertNotIn(rowid, self._matches('"saffron"'))

    def test_cascade_delete_removes_descendant_documents(self):
        folder = File.objects.create(
            name="Kitchen", node_type=File.NodeType.FOLDER, owner=self.user
        )
        note = self._note("groceries.md", b"buy some saffron")
        note.parent = folder
        note.save()
        index_file(note)
        rowid = self._rowid(note)
        folder.delete(hard=True)
        self.assertNotIn(rowid, self._matches('"saffron"'))

    def test_an_unreadable_blob_still_indexes_the_name(self):
        note = self._note("recipes.md", b"buy some saffron")
        note.content.storage.delete(note.content.name)
        self.assertTrue(index_file(note))
        self.assertEqual(self._matches('"recipes"'), {self._rowid(note)})

    def test_index_file_swallows_backend_failures(self):
        note = self._note("groceries.md", b"x")
        with mock.patch(
            "workspace.files.services.search_index.index_document",
            side_effect=RuntimeError("boom"),
        ):
            self.assertFalse(index_file(note))


class ReindexCommandTests(_FtsTestCase):
    def test_backfills_existing_files(self):
        note = self._note("old.md", b"forgotten wisdom")
        out = StringIO()
        call_command("reindex_files_search", stdout=out)
        self.assertEqual(self._matches('"wisdom"'), {self._rowid(note)})
        self.assertIn("Indexed", out.getvalue())

    def test_trashed_files_are_skipped_by_default(self):
        note = self._note("old.md", b"forgotten wisdom")
        note.soft_delete()
        call_command("reindex_files_search", stdout=StringIO())
        self.assertEqual(self._matches('"wisdom"'), set())

    def test_include_trashed_covers_them(self):
        note = self._note("old.md", b"forgotten wisdom")
        note.soft_delete()
        call_command("reindex_files_search", "--include-trashed", stdout=StringIO())
        self.assertEqual(self._matches('"wisdom"'), {self._rowid(note)})

    def test_owner_filter_narrows_the_backfill(self):
        mine = self._note("mine.md", b"alpha secret")
        other = User.objects.create_user(username="bob", password="pw")
        theirs = File.objects.create(
            name="theirs.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            owner=other,
            content=ContentFile(b"beta secret", name="theirs.md"),
        )
        call_command("reindex_files_search", "--owner", "alice", stdout=StringIO())
        self.assertEqual(self._matches('"alpha"'), {self._rowid(mine)})
        self.assertEqual(self._matches('"beta"'), set())
        self.assertTrue(theirs.pk)


class EventHandlerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.file = File.objects.create(
            name="note.md", node_type=File.NodeType.FILE, owner=self.user
        )

    def _run(self, action):
        from workspace.files.services.search_events import (
            index_search_document_for_event,
        )

        event = FileEvent.objects.create(file=self.file, actor=self.user, action=action)
        with mock.patch("workspace.files.tasks.index_search_document.delay") as delay:
            index_search_document_for_event(event)
        return delay

    def test_content_replaced_queues_a_reindex(self):
        delay = self._run(FileEvent.Action.CONTENT_REPLACED)
        delay.assert_called_once_with(str(self.file.pk), False)

    def test_rename_queues_a_reindex(self):
        delay = self._run(FileEvent.Action.RENAMED)
        delay.assert_called_once_with(str(self.file.pk), False)

    def test_create_also_covers_descendants(self):
        # A copied folder records one CREATED event for its root; the copied
        # subtree would otherwise stay unindexed.
        delay = self._run(FileEvent.Action.CREATED)
        delay.assert_called_once_with(str(self.file.pk), True)


class IndexTaskTests(_FtsTestCase):
    def test_unknown_uuid_is_reported_not_raised(self):
        from workspace.files.tasks import index_search_document

        result = index_search_document("00000000-0000-0000-0000-000000000000", False)
        self.assertEqual(result["status"], "not_found")

    def test_malformed_uuid_is_reported_not_raised(self):
        from workspace.files.tasks import index_search_document

        self.assertEqual(index_search_document("not-a-uuid")["status"], "not_found")

    def test_descendants_are_indexed_with_the_root(self):
        from workspace.files.tasks import index_search_document

        folder = File.objects.create(
            name="Kitchen", node_type=File.NodeType.FOLDER, owner=self.user
        )
        File.objects.create(
            name="child.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            parent=folder,
            owner=self.user,
            content=ContentFile(b"nested cardamom", name="child.md"),
        )
        result = index_search_document(str(folder.pk), True)
        self.assertEqual(result["indexed"], 2)
        with connection.cursor() as c:
            c.execute(
                f"SELECT count(*) FROM {FILES_FTS.fts_table} "
                f"WHERE {FILES_FTS.fts_table} MATCH %s",
                ['"cardamom"'],
            )
            self.assertEqual(c.fetchone()[0], 1)
