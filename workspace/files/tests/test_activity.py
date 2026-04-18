from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.activity import FilesActivityProvider
from workspace.files.models import File, FileShare

User = get_user_model()


class FilesActivityProviderTests(TestCase):

    def setUp(self):
        self.alice = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )
        self.bob = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )

        # Alice owns 2 files
        self.alice_file1 = File.objects.create(
            owner=self.alice, name='alice_doc.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain', size=100,
        )
        self.alice_file2 = File.objects.create(
            owner=self.alice, name='alice_notes.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain', size=200,
        )

        # Bob owns 1 file
        self.bob_file = File.objects.create(
            owner=self.bob, name='bob_sheet.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain', size=150,
        )

        # Alice shares file1 with bob
        FileShare.objects.create(
            file=self.alice_file1,
            shared_by=self.alice,
            shared_with=self.bob,
        )

        self.provider = FilesActivityProvider()

    # ── get_daily_counts ──────────────────────────────────

    def test_daily_counts_own_profile(self):
        """Alice viewing her own profile sees her 2 files."""
        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today,
        )
        self.assertEqual(counts.get(today, 0), 2)

    def test_daily_counts_viewer_sees_only_shared(self):
        """Bob looking at Alice's activity only sees the 1 shared file."""
        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today, viewer_id=self.bob.id,
        )
        self.assertEqual(counts.get(today, 0), 1)

    # ── get_recent_events ─────────────────────────────────

    def test_recent_events_own_profile(self):
        """Alice viewing her own profile sees her 2 files."""
        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 2)
        names = {e['description'] for e in events}
        self.assertEqual(names, {'alice_doc.txt', 'alice_notes.txt'})

    def test_recent_events_viewer_sees_only_shared(self):
        """Bob looking at Alice's activity only sees the 1 shared file."""
        events = self.provider.get_recent_events(
            self.alice.id, viewer_id=self.bob.id,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['description'], 'alice_doc.txt')

    def test_recent_events_limit_offset(self):
        """Pagination: limit and offset restrict returned events."""
        events = self.provider.get_recent_events(
            self.alice.id, limit=1, offset=0,
        )
        self.assertEqual(len(events), 1)

        events = self.provider.get_recent_events(
            self.alice.id, limit=1, offset=1,
        )
        self.assertEqual(len(events), 1)

        events = self.provider.get_recent_events(
            self.alice.id, limit=10, offset=2,
        )
        self.assertEqual(len(events), 0)

    # ── get_stats ─────────────────────────────────────────

    def test_stats_own_profile(self):
        """Alice's stats show her 2 files."""
        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats['total_files'], 2)

    def test_stats_viewer_sees_only_shared(self):
        """Bob looking at Alice's stats only sees the 1 shared file."""
        stats = self.provider.get_stats(
            self.alice.id, viewer_id=self.bob.id,
        )
        self.assertEqual(stats['total_files'], 1)

    # ── deleted files excluded ────────────────────────────

    def test_deleted_files_excluded(self):
        """Soft-deleted files should not appear in any results."""
        File.objects.create(
            owner=self.alice, name='deleted_file.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain', size=50,
            deleted_at=timezone.now(),
        )

        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today,
        )
        self.assertEqual(counts.get(today, 0), 2)  # still 2, deleted excluded

        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 2)

        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats['total_files'], 2)
