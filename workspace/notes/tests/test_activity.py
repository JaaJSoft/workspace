from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File, FileShare
from workspace.notes.activity import NotesActivityProvider

User = get_user_model()


class NotesActivityProviderTests(TestCase):

    def setUp(self):
        self.alice = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )
        self.bob = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )

        self.ts = timezone.now()

        # Alice: 2 markdown notes
        self.alice_note1 = File.objects.create(
            owner=self.alice,
            name='Alice Note 1',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )
        self.alice_note2 = File.objects.create(
            owner=self.alice,
            name='Alice Note 2',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )

        # Bob: 1 markdown note
        self.bob_note = File.objects.create(
            owner=self.bob,
            name='Bob Note 1',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )

        # Alice: 1 non-markdown file (should be excluded)
        self.alice_file = File.objects.create(
            owner=self.alice,
            name='Alice Image.png',
            node_type=File.NodeType.FILE,
            mime_type='image/png',
        )

        # Alice shares note1 with Bob
        FileShare.objects.create(
            file=self.alice_note1,
            shared_by=self.alice,
            shared_with=self.bob,
        )

        self.provider = NotesActivityProvider()

    # -- get_daily_counts ------------------------------------------------

    def test_daily_counts_own_profile(self):
        """Alice viewing her own profile sees 2 notes."""
        today = self.ts.date()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today,
        )
        self.assertEqual(counts.get(today, 0), 2)

    def test_daily_counts_viewer_sees_only_shared(self):
        """Bob looking at Alice's activity sees only 1 shared note."""
        today = self.ts.date()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today, viewer_id=self.bob.id,
        )
        self.assertEqual(counts.get(today, 0), 1)

    # -- get_recent_events -----------------------------------------------

    def test_recent_events_own_profile(self):
        """Alice sees 2 events on her own profile."""
        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 2)

    def test_recent_events_viewer_sees_only_shared(self):
        """Bob sees only 1 event for Alice's shared note."""
        events = self.provider.get_recent_events(
            self.alice.id, viewer_id=self.bob.id,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['description'], 'Alice Note 1')

    def test_recent_events_excludes_non_markdown(self):
        """Non-markdown files never appear in notes activity."""
        events = self.provider.get_recent_events(self.alice.id)
        descriptions = [e['description'] for e in events]
        self.assertNotIn('Alice Image.png', descriptions)

    # -- get_stats -------------------------------------------------------

    def test_stats_own_profile(self):
        """Alice gets total_notes=2 on her own profile."""
        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats['total_notes'], 2)

    def test_stats_viewer_sees_only_shared(self):
        """Bob looking at Alice gets total_notes=1."""
        stats = self.provider.get_stats(
            self.alice.id, viewer_id=self.bob.id,
        )
        self.assertEqual(stats['total_notes'], 1)

    # -- deleted notes excluded ------------------------------------------

    def test_deleted_notes_excluded(self):
        """Soft-deleted notes don't appear in any results."""
        File.objects.create(
            owner=self.alice,
            name='Deleted Note',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
            deleted_at=timezone.now(),
        )

        today = self.ts.date()

        # daily counts still 2
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today,
        )
        self.assertEqual(counts.get(today, 0), 2)

        # recent events still 2
        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 2)

        # stats still 2
        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats['total_notes'], 2)
