from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.models import File
from workspace.notes.search import search_notes

User = get_user_model()


class SearchNotesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username='alice', password='pass')
        cls.bob = User.objects.create_user(username='bob', password='pass')

        # Alice's parent folder (used as a tag on search results).
        cls.folder = File.objects.create(
            owner=cls.alice,
            name='Daily',
            node_type=File.NodeType.FOLDER,
        )

        cls.alice_note = File.objects.create(
            owner=cls.alice,
            parent=cls.folder,
            name='Meeting Notes',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )
        cls.alice_orphan_note = File.objects.create(
            owner=cls.alice,
            name='Grocery List',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )
        cls.alice_non_md = File.objects.create(
            owner=cls.alice,
            name='Notes Screenshot.png',
            node_type=File.NodeType.FILE,
            mime_type='image/png',
        )
        cls.bob_note = File.objects.create(
            owner=cls.bob,
            name='Bob Secret Notes',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )

    def test_returns_markdown_notes_matching_query(self):
        results = search_notes('meeting', self.alice, limit=10)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], SearchResult)
        self.assertEqual(results[0].name, 'Meeting Notes')
        self.assertEqual(results[0].module_slug, 'notes')
        self.assertEqual(results[0].match_type, 'name')
        self.assertEqual(results[0].matched_value, 'Meeting Notes')
        self.assertEqual(results[0].url, f'/notes?file={self.alice_note.uuid}')

    def test_search_is_case_insensitive(self):
        results = search_notes('MEETING', self.alice, limit=10)
        self.assertEqual(len(results), 1)

    def test_parent_folder_surfaced_as_tag(self):
        results = search_notes('meeting', self.alice, limit=10)
        self.assertEqual(len(results[0].tags), 1)
        self.assertIsInstance(results[0].tags[0], SearchTag)
        self.assertEqual(results[0].tags[0].label, 'Daily')
        self.assertEqual(results[0].tags[0].color, 'success')

    def test_orphan_note_has_no_tags(self):
        results = search_notes('grocery', self.alice, limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].tags, ())

    def test_excludes_non_markdown_files(self):
        # "Notes Screenshot.png" matches "notes" by name but is not markdown.
        results = search_notes('screenshot', self.alice, limit=10)
        self.assertEqual(results, [])

    def test_excludes_other_users_notes(self):
        # Bob's markdown note must not leak to Alice.
        results = search_notes('secret', self.alice, limit=10)
        self.assertEqual(results, [])

    def test_limit_is_respected(self):
        # Create enough markdown notes to exceed the limit.
        for i in range(5):
            File.objects.create(
                owner=self.alice,
                name=f'Bulk Note {i}',
                node_type=File.NodeType.FILE,
                mime_type='text/markdown',
            )
        results = search_notes('bulk', self.alice, limit=3)
        self.assertEqual(len(results), 3)

    def test_empty_query_returns_all_markdown_notes_within_limit(self):
        # Empty string is treated as icontains='' which matches everything.
        results = search_notes('', self.alice, limit=10)
        names = {r.name for r in results}
        self.assertIn('Meeting Notes', names)
        self.assertIn('Grocery List', names)
        self.assertNotIn('Notes Screenshot.png', names)
        self.assertNotIn('Bob Secret Notes', names)
