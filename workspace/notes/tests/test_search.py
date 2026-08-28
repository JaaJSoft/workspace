from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase

from workspace.common.search import fts5_available
from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.models import File
from workspace.files.services.search_index import index_file
from workspace.notes.search import search_notes

User = get_user_model()


class SearchNotesTests(TestCase):
    """Search reads the full-text index, which the indexing task writes.

    Nothing dispatches that task here (Celery is not eager under test), so
    every fixture indexes itself explicitly - the same thing the backfill
    command does for an existing install.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")

        # Alice's parent folder (used as a tag on search results).
        self.folder = self._note(self.alice, "Daily", folder=True)
        self.alice_note = self._note(
            self.alice,
            "Meeting Notes",
            parent=self.folder,
            body=b"Discussed the kraken migration plan.",
        )
        self.alice_orphan_note = self._note(self.alice, "Grocery List")
        self.alice_non_md = self._note(
            self.alice, "Notes Screenshot.png", mime="image/png"
        )
        self.bob_note = self._note(self.bob, "Bob Secret Notes")

    def _note(self, owner, name, *, parent=None, folder=False, mime=None, body=None):
        file_obj = File.objects.create(
            owner=owner,
            parent=parent,
            name=name,
            node_type=File.NodeType.FOLDER if folder else File.NodeType.FILE,
            mime_type=None if folder else (mime or "text/markdown"),
            content=ContentFile(body, name=name) if body else None,
        )
        index_file(file_obj)
        return file_obj

    def test_returns_markdown_notes_matching_query(self):
        results = search_notes("meeting", self.alice, limit=10)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], SearchResult)
        self.assertEqual(results[0].name, "Meeting Notes")
        self.assertEqual(results[0].module_slug, "notes")
        self.assertEqual(results[0].match_type, "name")
        self.assertEqual(results[0].matched_value, "Meeting Notes")
        self.assertEqual(results[0].url, f"/notes?file={self.alice_note.uuid}")

    def test_search_is_case_insensitive(self):
        results = search_notes("MEETING", self.alice, limit=10)
        self.assertEqual(len(results), 1)

    def test_parent_folder_surfaced_as_tag(self):
        results = search_notes("meeting", self.alice, limit=10)
        self.assertEqual(len(results[0].tags), 1)
        self.assertIsInstance(results[0].tags[0], SearchTag)
        self.assertEqual(results[0].tags[0].label, "Daily")
        self.assertEqual(results[0].tags[0].color, "success")

    def test_orphan_note_has_no_tags(self):
        results = search_notes("grocery", self.alice, limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].tags, ())

    def test_excludes_non_markdown_files(self):
        # "Notes Screenshot.png" matches "notes" by name but is not markdown.
        results = search_notes("screenshot", self.alice, limit=10)
        self.assertEqual(results, [])

    def test_excludes_other_users_notes(self):
        # Bob's markdown note must not leak to Alice.
        results = search_notes("secret", self.alice, limit=10)
        self.assertEqual(results, [])

    def test_limit_is_respected(self):
        for i in range(5):
            self._note(self.alice, f"Bulk Note {i}")
        results = search_notes("bulk", self.alice, limit=3)
        self.assertEqual(len(results), 3)

    def test_empty_query_returns_nothing(self):
        # A blank query matches no lexeme. The user-facing entry point
        # (core.services.search) refuses queries shorter than 2 chars anyway.
        self.assertEqual(search_notes("", self.alice, limit=10), [])
        self.assertEqual(search_notes("   ", self.alice, limit=10), [])

    def test_a_phrase_only_in_the_body_finds_the_note(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        results = search_notes("kraken", self.alice, limit=10)
        self.assertEqual([r.name for r in results], ["Meeting Notes"])
        self.assertEqual(results[0].match_type, "content")

    def test_name_search_is_accent_insensitive(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        self._note(self.alice, "Réunion trimestrielle")
        results = search_notes("reunion", self.alice, limit=10)
        self.assertEqual([r.name for r in results], ["Réunion trimestrielle"])
        self.assertEqual(results[0].match_type, "name")

    def test_a_name_match_outranks_a_body_only_match(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        self._note(self.alice, "Kraken sightings")
        results = search_notes("kraken", self.alice, limit=10)
        self.assertEqual(
            [r.name for r in results], ["Kraken sightings", "Meeting Notes"]
        )
