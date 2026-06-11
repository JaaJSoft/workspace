from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File, FileEvent, FileShare
from workspace.files.services.events import record_event
from workspace.notes.activity import NotesActivityProvider

User = get_user_model()


class NotesActivityProviderTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice",
            email="alice@test.com",
            password="pass123",
        )
        self.bob = User.objects.create_user(
            username="bob",
            email="bob@test.com",
            password="pass123",
        )

        # Alice: 2 markdown notes
        self.alice_note1 = File.objects.create(
            owner=self.alice,
            name="Alice Note 1",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )
        self.alice_note2 = File.objects.create(
            owner=self.alice,
            name="Alice Note 2",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )

        # Bob: 1 markdown note
        self.bob_note = File.objects.create(
            owner=self.bob,
            name="Bob Note 1",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )

        # Alice: 1 non-markdown file - belongs to the files provider, never notes
        self.alice_file = File.objects.create(
            owner=self.alice,
            name="Alice Image.png",
            node_type=File.NodeType.FILE,
            mime_type="image/png",
        )

        # Alice shares note1 with Bob
        FileShare.objects.create(
            file=self.alice_note1,
            shared_by=self.alice,
            shared_with=self.bob,
        )

        # One event per note - the provider reads FileEvent, not File rows.
        record_event(self.alice_note1, self.alice, FileEvent.Action.CONTENT_REPLACED)
        record_event(self.alice_note2, self.alice, FileEvent.Action.CREATED)
        record_event(self.bob_note, self.bob, FileEvent.Action.CREATED)
        # The png event must never surface in notes activity.
        record_event(self.alice_file, self.alice, FileEvent.Action.CREATED)

        self.provider = NotesActivityProvider()

    # -- get_daily_counts ------------------------------------------------

    def test_daily_counts_own_profile(self):
        """Alice viewing her own profile sees her 2 note events."""
        today = date.today()
        counts = self.provider.get_daily_counts(self.alice.id, today, today)
        self.assertEqual(counts.get(today, 0), 2)

    def test_daily_counts_counts_each_event(self):
        """Multiple events on the same note each count (event-level feed)."""
        record_event(self.alice_note1, self.alice, FileEvent.Action.RENAMED)

        today = date.today()
        counts = self.provider.get_daily_counts(self.alice.id, today, today)
        # 2 from setUp + 1 new = 3.
        self.assertEqual(counts.get(today, 0), 3)

    def test_daily_counts_excludes_non_markdown(self):
        """The png event never inflates the notes grid."""
        today = date.today()
        counts = self.provider.get_daily_counts(self.alice.id, today, today)
        self.assertEqual(counts.get(today, 0), 2)

    def test_daily_counts_viewer_sees_only_shared(self):
        """Bob looking at Alice's activity sees only the 1 shared note's event."""
        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id,
            today,
            today,
            viewer_id=self.bob.id,
        )
        self.assertEqual(counts.get(today, 0), 1)

    # -- get_recent_events -----------------------------------------------

    def test_recent_events_own_profile(self):
        """Alice sees her 2 note events on her own profile."""
        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 2)
        self.assertEqual(
            {e["description"] for e in events},
            {"Alice Note 1", "Alice Note 2"},
        )

    def test_recent_events_excludes_non_markdown(self):
        """Non-markdown files never appear in notes activity."""
        events = self.provider.get_recent_events(self.alice.id)
        self.assertNotIn("Alice Image.png", {e["description"] for e in events})

    def test_recent_events_viewer_sees_only_shared(self):
        """Bob sees only the event for Alice's shared note."""
        events = self.provider.get_recent_events(
            self.alice.id,
            viewer_id=self.bob.id,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["description"], "Alice Note 1")

    def test_recent_events_viewer_sees_group_shared_note(self):
        """A note Alice owns in a group Bob belongs to is accessible to Bob, so
        Bob must see its events even without a direct FileShare - event access
        follows file access, which includes group membership."""
        from django.contrib.auth.models import Group

        team = Group.objects.create(name="team")
        self.bob.groups.add(team)
        group_note = File.objects.create(
            owner=self.alice,
            name="Group Note",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            group=team,
        )
        record_event(group_note, self.alice, FileEvent.Action.CONTENT_REPLACED)

        events = self.provider.get_recent_events(
            self.alice.id,
            viewer_id=self.bob.id,
        )

        self.assertIn("Group Note", {e["description"] for e in events})

    def test_recent_events_url_opens_note(self):
        """The activity link opens the note in the notes app."""
        events = self.provider.get_recent_events(self.alice.id)
        urls = {e["description"]: e["url"] for e in events}
        self.assertEqual(urls["Alice Note 1"], f"/notes?file={self.alice_note1.uuid}")

    def test_recent_events_uses_action_specific_label(self):
        """Each event reports its own action label, not a generic one."""
        FileEvent.objects.all().delete()
        record_event(self.alice_note1, self.alice, FileEvent.Action.RENAMED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["label"], "Renamed")
        self.assertEqual(events[0]["icon"], "pencil")

    def test_recent_events_emit_null_actor_for_system_events(self):
        """System actions (no actor) emit a null actor in the feed entry."""
        FileEvent.objects.all().delete()
        record_event(self.alice_note1, None, FileEvent.Action.DELETED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["actor"])

    def test_recent_events_actor_can_differ_from_owner(self):
        """When Bob edits a shared note, the event's actor is Bob, not Alice."""
        FileEvent.objects.all().delete()
        record_event(self.alice_note1, self.bob, FileEvent.Action.CONTENT_REPLACED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"]["username"], "bob")

    # -- get_stats -------------------------------------------------------

    def test_stats_own_profile(self):
        """Alice gets total_notes=2 on her own profile."""
        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats["total_notes"], 2)

    def test_stats_viewer_sees_only_shared(self):
        """Bob looking at Alice gets total_notes=1."""
        stats = self.provider.get_stats(self.alice.id, viewer_id=self.bob.id)
        self.assertEqual(stats["total_notes"], 1)

    # -- deleted notes: events kept in feed, stats exclude ---------------

    def test_events_on_deleted_notes_are_kept_in_feed(self):
        """Events stay in the feed after a note is trashed, so the Trashed
        event itself stays reachable (mirrors the files provider). Only
        ``get_stats`` gates on ``deleted_at`` because it counts current notes."""
        deleted = File.objects.create(
            owner=self.alice,
            name="Deleted Note",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            deleted_at=timezone.now(),
        )
        record_event(deleted, self.alice, FileEvent.Action.DELETED)

        today = date.today()
        counts = self.provider.get_daily_counts(self.alice.id, today, today)
        self.assertEqual(counts.get(today, 0), 3)

        events = self.provider.get_recent_events(self.alice.id)
        self.assertIn("Deleted Note", {e["description"] for e in events})

        # Stats count current notes only - the trashed one is excluded.
        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats["total_notes"], 2)
