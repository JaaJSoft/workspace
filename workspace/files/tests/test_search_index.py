from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase

from workspace.common.search import apply_fulltext, fts5_available
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
        file_obj.refresh_from_db(fields=["fts_rowid"])
        return file_obj.fts_rowid


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

    def test_unindex_file_swallows_backend_failures(self):
        # A failed unindex must not break the delete it is hooked into.
        note = self._note("groceries.md", b"x")
        with mock.patch(
            "workspace.files.services.search_index.drop_document",
            side_effect=RuntimeError("boom"),
        ):
            self.assertFalse(unindex_file(note))

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

    def test_every_page_is_covered(self):
        # The backfill pages by keyset rather than streaming a cursor, so it
        # is worth pinning that the paging itself walks the whole table: an
        # off-by-one in the "uuid > last" bound silently skips or repeats.
        notes = [self._note(f"n{i}.md", f"needle{i}".encode()) for i in range(7)]
        call_command("reindex_files_search", "--batch-size", "2", stdout=StringIO())
        for i, note in enumerate(notes):
            with self.subTest(i=i):
                self.assertEqual(self._matches(f'"needle{i}"'), {self._rowid(note)})

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


class ContentIsNeverPersistedTests(_FtsTestCase):
    """The blob stays the single copy of the content.

    What lands in the database is an inverted index, never the prose: the
    lexemes are there by design, the document is not recoverable from them,
    and no ordinary column ever receives the text.
    """

    PHRASE = "zqxjvwphrase"

    def _indexed_note(self):
        note = self._note("minutes.md", f"the {self.PHRASE} was noted".encode())
        index_file(note)
        return note

    def test_the_document_cannot_be_read_back(self):
        # A contentless FTS5 table answers every column with NULL: it knows
        # which rows contain a term, not what those rows said.
        note = self._indexed_note()
        with connection.cursor() as c:
            c.execute(
                f"SELECT name, body FROM {FILES_FTS.fts_table} WHERE rowid = %s",
                [self._rowid(note)],
            )
            self.assertEqual(c.fetchone(), (None, None))

    def test_the_fts5_table_has_no_content_shadow_table(self):
        # The shadow table FTS5 creates for an external-content index is what
        # would hold the text; content='' must keep it from existing.
        with connection.cursor() as c:
            c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s",
                [f"{FILES_FTS.fts_table}_content"],
            )
            self.assertIsNone(c.fetchone())

    def test_no_ordinary_table_holds_the_text(self):
        # Guards against the obvious regression: a `search_text` column added
        # to files_file, or any other second copy of the prose. FTS shadow
        # tables are excluded - storing lexemes is their job.
        self._indexed_note()
        with connection.cursor() as c:
            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%%' AND name NOT LIKE %s",
                [f"{FILES_FTS.fts_table}%"],
            )
            tables = [row[0] for row in c.fetchall()]
            offenders = [t for t in tables if self._table_contains(c, t, self.PHRASE)]
        self.assertEqual(offenders, [])

    @staticmethod
    def _table_contains(cursor, table, needle):
        cursor.execute(f'SELECT * FROM "{table}"')
        for row in cursor.fetchall():
            for value in row:
                if isinstance(value, str) and needle in value:
                    return True
                if isinstance(value, bytes) and needle.encode() in value:
                    return True
        return False


class SurvivesATableRebuildTests(TransactionTestCase):
    """The index must outlive a migration that rewrites files_file.

    Django's SQLite schema editor rebuilds a table for many operations - any
    AddField reaches it - by creating a copy, moving the rows across and
    renaming. Implicit rowids are NOT carried over, so an index keyed on them
    silently starts answering with the wrong files. TransactionTestCase
    because the rebuild commits.
    """

    def setUp(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        self.user = User.objects.create_user(username="alice", password="pw")

    def _note(self, name, body):
        note = File.objects.create(
            name=name,
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            owner=self.user,
            content=ContentFile(body, name=name),
        )
        index_file(note)
        return note

    def _search(self, term):
        return [
            f.name
            for f in apply_fulltext(
                File.objects.filter(owner=self.user), term, index=FILES_FTS
            )
        ]

    @staticmethod
    def _rebuild_files_file():
        """What Django's SQLite backend does for an AddField, in miniature."""
        with connection.cursor() as c:
            c.execute("SELECT sql FROM sqlite_master WHERE name = 'files_file'")
            create_sql = c.fetchone()[0].replace("files_file", "new__files_file", 1)
            c.execute("SELECT name FROM pragma_table_info('files_file')")
            cols = ", ".join(f'"{row[0]}"' for row in c.fetchall())
            c.execute(create_sql)
            # ORDER BY reverses the insertion order, so every implicit rowid
            # moves - the same outcome as a real rebuild, made deterministic.
            c.execute(
                f"INSERT INTO new__files_file ({cols}) "
                f"SELECT {cols} FROM files_file ORDER BY name DESC"
            )
            c.execute("DROP TABLE files_file")
            c.execute("ALTER TABLE new__files_file RENAME TO files_file")

    def test_search_still_finds_the_right_file_after_a_rebuild(self):
        self._note("alpha.md", b"the kraken sleeps")
        self._note("beta.md", b"the marina is closed")
        self.assertEqual(self._search("kraken"), ["alpha.md"])

        self._rebuild_files_file()

        self.assertEqual(self._search("kraken"), ["alpha.md"])
        self.assertEqual(self._search("marina"), ["beta.md"])

    def test_the_implicit_rowids_really_did_move(self):
        # Guards the guard: if a future Django stopped reassigning rowids the
        # test above would pass for the wrong reason and stop proving anything.
        self._note("alpha.md", b"body one")
        self._note("beta.md", b"body two")
        before = self._implicit_rowids()
        self._rebuild_files_file()
        self.assertNotEqual(before, self._implicit_rowids())

    @staticmethod
    def _implicit_rowids():
        with connection.cursor() as c:
            c.execute("SELECT uuid, rowid FROM files_file ORDER BY uuid")
            return dict(c.fetchall())


class KeyAssignmentTests(_FtsTestCase):
    """The FTS key is derived from the uuid, and the database arbitrates it."""

    def test_the_key_is_derived_from_the_uuid(self):
        note = self._note("a.md", b"body")
        index_file(note)
        note.refresh_from_db()
        self.assertEqual(note.fts_rowid, note.uuid.int & ((1 << 63) - 1))

    def test_reindexing_keeps_the_same_key(self):
        note = self._note("a.md", b"body")
        index_file(note)
        note.refresh_from_db()
        first = note.fts_rowid
        index_file(note)
        note.refresh_from_db()
        self.assertEqual(note.fts_rowid, first)

    def test_a_taken_key_is_stepped_over_not_shared(self):
        # Two files sharing one key would silently merge their documents. The
        # unique constraint makes the database refuse it; the next candidate
        # is tried. Forced here by parking the derived key on another row.
        victim = self._note("victim.md", b"unrelated")
        note = self._note("note.md", b"a rare gorgonzola reference")
        File.objects.filter(pk=victim.pk).update(
            fts_rowid=note.uuid.int & ((1 << 63) - 1)
        )

        self.assertTrue(index_file(note))
        note.refresh_from_db()
        self.assertIsNotNone(note.fts_rowid)
        self.assertNotEqual(note.fts_rowid, victim.uuid.int & ((1 << 63) - 1))
        self.assertEqual(self._matches('"gorgonzola"'), {note.fts_rowid})
