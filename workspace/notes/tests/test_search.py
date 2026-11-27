from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from workspace.common.search import fts5_available
from workspace.core.module_registry import SearchResult, SearchScope, SearchTag
from workspace.core.services.search import search_modules
from workspace.files.models import File, FileShare
from workspace.files.services import tree
from workspace.files.services.search_index import index_file
from workspace.files.services.sharing import share_file
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


class SearchNotesReachTests(TestCase):
    """A note the user opens through a group or a share is a note too: it
    comes back from this provider, so it opens in the editor."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.carol = User.objects.create_user(username="carol", password="pass")
        team = Group.objects.create(name="Team")
        self.alice.groups.add(team)
        self.bob.groups.add(team)
        self.team_root = tree.create_folder(owner=self.bob, name="Team", group=team)
        bob_private = tree.create_folder(owner=self.bob, name="Bob private")

        self.personal = self._note(self.alice, "Quarterly plan mine")
        self.group_note = self._note(
            self.bob, "Quarterly plan team", parent=self.team_root, group=team
        )
        self.shared = self._note(self.bob, "Quarterly plan shared", parent=bob_private)
        share_file(
            self.shared,
            target_user=self.alice,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.bob,
        )
        self.unreachable = self._note(self.carol, "Quarterly plan carol")

    def _note(self, owner, name, *, parent=None, group=None):
        file_obj = File.objects.create(
            owner=owner,
            parent=parent,
            group=group,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )
        index_file(file_obj)
        return file_obj

    def _by_name(self, scope=SearchScope.ALL):
        return {
            r.name: r for r in search_notes("quarterly", self.alice, 10, scope=scope)
        }

    def test_group_and_shared_notes_open_in_the_editor(self):
        hits = self._by_name()
        self.assertEqual(
            set(hits),
            {"Quarterly plan mine", "Quarterly plan team", "Quarterly plan shared"},
        )
        self.assertEqual(
            hits["Quarterly plan team"].url, f"/notes?file={self.group_note.uuid}"
        )
        self.assertEqual(
            hits["Quarterly plan shared"].url, f"/notes?file={self.shared.uuid}"
        )

    def test_mine_keeps_personal_notes_only(self):
        self.assertEqual(set(self._by_name(SearchScope.MINE)), {"Quarterly plan mine"})

    def test_a_note_is_tagged_only_with_a_folder_the_user_can_open(self):
        hits = self._by_name()
        self.assertEqual(
            hits["Quarterly plan team"].tags, (SearchTag("Team", "success"),)
        )
        # The share reaches the note, not Bob's private folder above it.
        self.assertEqual(hits["Quarterly plan shared"].tags, ())

    def test_trashed_shared_note_is_not_returned(self):
        self.shared.deleted_at = timezone.now()
        self.shared.save()
        self.assertNotIn("Quarterly plan shared", self._by_name())

    def test_unified_search_lists_each_reachable_note_once_under_notes(self):
        hits = [
            h
            for h in search_modules("quarterly", self.alice)
            if h["uuid"] in {str(self.group_note.uuid), str(self.shared.uuid)}
        ]
        self.assertEqual(
            sorted((h["uuid"], h["provider_slug"]) for h in hits),
            sorted(
                [
                    (str(self.group_note.uuid), "notes"),
                    (str(self.shared.uuid), "notes"),
                ]
            ),
        )
