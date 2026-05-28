from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.activity import FilesActivityProvider
from workspace.files.models import File, FileEvent, FileShare
from workspace.files.services.events import record_event

User = get_user_model()


class FilesActivityProviderTests(TestCase):

    def setUp(self):
        self.alice = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )
        self.bob = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )

        # Alice owns 2 files, each with one CREATED event.
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

        # Seed one event per file - direct `File.objects.create` does not
        # write events, so the tests would otherwise see an empty feed.
        record_event(self.alice_file1, self.alice, FileEvent.Action.CREATED)
        record_event(self.alice_file2, self.alice, FileEvent.Action.CREATED)
        record_event(self.bob_file, self.bob, FileEvent.Action.CREATED)

        self.provider = FilesActivityProvider()

    # ── get_daily_counts ──────────────────────────────────

    def test_daily_counts_own_profile(self):
        """Alice viewing her own profile sees her 2 events."""
        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today,
        )
        self.assertEqual(counts.get(today, 0), 2)

    def test_daily_counts_viewer_sees_only_shared(self):
        """Bob looking at Alice's activity only sees the 1 shared event."""
        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today, viewer_id=self.bob.id,
        )
        self.assertEqual(counts.get(today, 0), 1)

    def test_daily_counts_count_each_event(self):
        """Multiple events on the same file produce multiple counts."""
        record_event(self.alice_file1, self.alice, FileEvent.Action.RENAMED)
        record_event(self.alice_file1, self.alice, FileEvent.Action.CONTENT_REPLACED)

        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today,
        )
        # 2 CREATED events from setUp + 2 new events on file1 = 4
        self.assertEqual(counts.get(today, 0), 4)

    # ── get_recent_events ─────────────────────────────────

    def test_recent_events_own_profile(self):
        """Alice viewing her own profile sees her 2 file events."""
        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 2)
        names = {e['description'] for e in events}
        self.assertEqual(names, {'alice_doc.txt', 'alice_notes.txt'})

    def test_recent_events_url_opens_file_viewer(self):
        """The activity link must open the file viewer via ?open=. The files
        app never reads ?preview=, so the old link silently landed on the
        files root instead of opening the file."""
        events = self.provider.get_recent_events(self.alice.id)
        urls = {e['description']: e['url'] for e in events}
        self.assertEqual(urls['alice_doc.txt'], f'/files?open={self.alice_file1.uuid}')
        for url in urls.values():
            self.assertNotIn('preview=', url)

    def test_recent_events_viewer_sees_only_shared(self):
        """Bob looking at Alice's activity only sees the 1 shared file event."""
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

    def test_recent_events_uses_action_specific_label(self):
        """Each event reports its own action label, not a generic one."""
        FileEvent.objects.all().delete()
        record_event(self.alice_file1, self.alice, FileEvent.Action.RENAMED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertEqual(events[0]['label'], 'Renamed')
        self.assertEqual(events[0]['icon'], 'pencil')

    def test_recent_events_actor_can_differ_from_owner(self):
        """When Bob (rw share) replaces content, the event's actor is Bob, not Alice."""
        FileEvent.objects.all().delete()
        record_event(self.alice_file1, self.bob, FileEvent.Action.CONTENT_REPLACED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['actor']['username'], 'bob')

    def test_recent_events_emit_null_actor_for_system_events(self):
        """System actions (no actor) emit a null actor in the feed entry.

        Falsely attributing them to the file owner would lie to the user
        ('Alice trashed file.txt' when actually a Celery task or the sync
        service did it). The dashboard template renders the actor block
        only when the field is non-null.
        """
        FileEvent.objects.all().delete()
        record_event(self.alice_file1, None, FileEvent.Action.DELETED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]['actor'])

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

    # ── deleted files: events kept, stats exclude ────────────

    def test_events_on_deleted_files_are_kept_in_feed(self):
        """Events stay in the activity feed even after the file is trashed.

        The feed is a historical audit log: hiding events for soft-deleted
        files would also hide the DELETED event itself (the file is in
        trash by the time the event lands). ``get_stats`` is the only path
        that still gates on ``deleted_at`` because it counts current files,
        not history.
        """
        deleted = File.objects.create(
            owner=self.alice, name='deleted_file.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain', size=50,
            deleted_at=timezone.now(),
        )
        record_event(deleted, self.alice, FileEvent.Action.CREATED)

        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today,
        )
        # 2 from setUp + 1 from the trashed file = 3.
        self.assertEqual(counts.get(today, 0), 3)

        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 3)
        self.assertIn('deleted_file.txt', {e['description'] for e in events})

        # Stats are about current state, not history - trashed files stay
        # excluded from the file count.
        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats['total_files'], 2)
