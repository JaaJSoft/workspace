from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.activity import FilesActivityProvider
from workspace.files.models import File, FileEvent, FileShare
from workspace.files.services.events import record_event

PARIS = ZoneInfo("Europe/Paris")

User = get_user_model()


class FilesActivityProviderTests(TestCase):
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

        # Alice owns 2 files, each with one CREATED event.
        self.alice_file1 = File.objects.create(
            owner=self.alice,
            name="alice_doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            size=100,
        )
        self.alice_file2 = File.objects.create(
            owner=self.alice,
            name="alice_notes.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            size=200,
        )

        # Bob owns 1 file
        self.bob_file = File.objects.create(
            owner=self.bob,
            name="bob_sheet.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            size=150,
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
            self.alice.id,
            today,
            today,
        )
        self.assertEqual(counts.get(today, 0), 2)

    def test_daily_counts_bounds_follow_the_active_timezone(self):
        """A day starts and ends at local midnight, not UTC midnight."""
        for hour in (21, 22):
            event = record_event(
                self.alice_file1, self.alice, FileEvent.Action.CONTENT_REPLACED
            )
            FileEvent.objects.filter(pk=event.pk).update(
                created_at=datetime(2026, 7, 5, hour, 30, tzinfo=UTC)
            )
        # In July Paris is UTC+2: 21:30 UTC is still July 5 there, 22:30 UTC is
        # already July 6.
        with timezone.override(PARIS):
            counts = self.provider.get_daily_counts(
                self.alice.id, date(2026, 7, 5), date(2026, 7, 5)
            )
        self.assertEqual(counts, {date(2026, 7, 5): 1})

    def test_daily_counts_viewer_sees_only_shared(self):
        """Bob looking at Alice's activity only sees the 1 shared event."""
        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id,
            today,
            today,
            viewer_id=self.bob.id,
        )
        self.assertEqual(counts.get(today, 0), 1)

    def test_daily_counts_count_each_event(self):
        """Multiple events on the same file produce multiple counts."""
        record_event(self.alice_file1, self.alice, FileEvent.Action.RENAMED)
        record_event(self.alice_file1, self.alice, FileEvent.Action.CONTENT_REPLACED)

        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id,
            today,
            today,
        )
        # 2 CREATED events from setUp + 2 new events on file1 = 4
        self.assertEqual(counts.get(today, 0), 4)

    # ── get_recent_events ─────────────────────────────────

    def test_recent_events_own_profile(self):
        """Alice viewing her own profile sees her 2 file events."""
        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 2)
        names = {e["description"] for e in events}
        self.assertEqual(names, {"alice_doc.txt", "alice_notes.txt"})

    def test_recent_events_url_opens_file_viewer(self):
        """The activity link must open the file viewer via ?open=. The files
        app never reads ?preview=, so the old link silently landed on the
        files root instead of opening the file."""
        events = self.provider.get_recent_events(self.alice.id)
        urls = {e["description"]: e["url"] for e in events}
        self.assertEqual(urls["alice_doc.txt"], f"/files?open={self.alice_file1.uuid}")
        for url in urls.values():
            self.assertNotIn("preview=", url)

    def test_recent_events_url_lands_in_parent_folder(self):
        """A file inside a folder links to that folder with the viewer opened
        (``/files/<folder>?open=``), mirroring the notes module's "Open in
        Files" so the browser lands next to the file instead of at the root."""
        folder = File.objects.create(
            owner=self.alice,
            name="Reports",
            node_type=File.NodeType.FOLDER,
        )
        nested = File.objects.create(
            owner=self.alice,
            name="q1.txt",
            parent=folder,
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            size=10,
        )
        FileEvent.objects.all().delete()
        record_event(nested, self.alice, FileEvent.Action.CREATED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["url"],
            f"/files/{folder.uuid}?open={nested.uuid}",
        )

    def test_recent_events_viewer_sees_only_shared(self):
        """Bob looking at Alice's activity only sees the 1 shared file event."""
        events = self.provider.get_recent_events(
            self.alice.id,
            viewer_id=self.bob.id,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["description"], "alice_doc.txt")

    def test_recent_events_viewer_sees_group_shared_file(self):
        """A file Alice owns in a group Bob belongs to is accessible to Bob, so
        Bob must see its events even without a direct FileShare - event access
        follows file access, which includes group membership."""
        from django.contrib.auth.models import Group

        team = Group.objects.create(name="team")
        self.bob.groups.add(team)
        group_file = File.objects.create(
            owner=self.alice,
            name="group_doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            size=10,
            group=team,
        )
        record_event(group_file, self.alice, FileEvent.Action.CONTENT_REPLACED)

        events = self.provider.get_recent_events(
            self.alice.id,
            viewer_id=self.bob.id,
        )

        self.assertIn("group_doc.txt", {e["description"] for e in events})

    def test_recent_events_limit_offset(self):
        """Pagination: limit and offset restrict returned events."""
        events = self.provider.get_recent_events(
            self.alice.id,
            limit=1,
            offset=0,
        )
        self.assertEqual(len(events), 1)

        events = self.provider.get_recent_events(
            self.alice.id,
            limit=1,
            offset=1,
        )
        self.assertEqual(len(events), 1)

        events = self.provider.get_recent_events(
            self.alice.id,
            limit=10,
            offset=2,
        )
        self.assertEqual(len(events), 0)

    def test_recent_events_uses_action_specific_label(self):
        """Each event reports its own action label, not a generic one."""
        FileEvent.objects.all().delete()
        record_event(self.alice_file1, self.alice, FileEvent.Action.RENAMED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertEqual(events[0]["label"], "Renamed")
        self.assertEqual(events[0]["icon"], "pencil")

    def test_recent_events_actor_can_differ_from_owner(self):
        """When Bob (rw share) replaces content, the event's actor is Bob, not Alice."""
        FileEvent.objects.all().delete()
        record_event(self.alice_file1, self.bob, FileEvent.Action.CONTENT_REPLACED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"]["username"], "bob")

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
        self.assertIsNone(events[0]["actor"])

    def test_recent_events_exclude_actor_before_the_limit(self):
        """The excluded actor's events never take a slot; null actors stay."""
        FileEvent.objects.all().delete()
        record_event(self.alice_file1, self.bob, FileEvent.Action.CONTENT_REPLACED)
        record_event(self.alice_file1, None, FileEvent.Action.DELETED)
        for _ in range(3):
            record_event(
                self.alice_file1, self.alice, FileEvent.Action.CONTENT_REPLACED
            )
        FileEvent.objects.filter(actor=self.alice).update(
            created_at=timezone.now() + timedelta(hours=1)
        )

        events = self.provider.get_recent_events(
            None, limit=2, exclude_actor_id=self.alice.id
        )

        self.assertCountEqual(
            [(e["actor"] or {}).get("id") for e in events], [None, self.bob.id]
        )

    # ── get_stats ─────────────────────────────────────────

    def test_stats_own_profile(self):
        """Alice's stats show her 2 files."""
        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats["total_files"], 2)

    def test_stats_viewer_sees_only_shared(self):
        """Bob looking at Alice's stats only sees the 1 shared file."""
        stats = self.provider.get_stats(
            self.alice.id,
            viewer_id=self.bob.id,
        )
        self.assertEqual(stats["total_files"], 1)

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
            owner=self.alice,
            name="deleted_file.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            size=50,
            deleted_at=timezone.now(),
        )
        record_event(deleted, self.alice, FileEvent.Action.CREATED)

        today = date.today()
        counts = self.provider.get_daily_counts(
            self.alice.id,
            today,
            today,
        )
        # 2 from setUp + 1 from the trashed file = 3.
        self.assertEqual(counts.get(today, 0), 3)

        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 3)
        self.assertIn("deleted_file.txt", {e["description"] for e in events})

        # Stats are about current state, not history - trashed files stay
        # excluded from the file count.
        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats["total_files"], 2)

    # ── markdown notes belong to the notes provider, not files ──

    def test_markdown_notes_excluded_from_feed(self):
        """Notes (markdown files) are surfaced by the notes provider, so the
        files provider must not also list them - otherwise a single note edit
        shows up twice in the combined activity feed."""
        note = File.objects.create(
            owner=self.alice,
            name="My Note",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )
        record_event(note, self.alice, FileEvent.Action.CONTENT_REPLACED)

        events = self.provider.get_recent_events(self.alice.id)

        self.assertNotIn("My Note", {e["description"] for e in events})

    def test_markdown_notes_excluded_from_daily_counts(self):
        """A note event must not inflate the files activity grid."""
        note = File.objects.create(
            owner=self.alice,
            name="My Note",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )
        record_event(note, self.alice, FileEvent.Action.CONTENT_REPLACED)

        today = date.today()
        counts = self.provider.get_daily_counts(self.alice.id, today, today)

        # Only the 2 CREATED events from setUp - the note event is excluded.
        self.assertEqual(counts.get(today, 0), 2)

    def test_markdown_notes_excluded_from_stats(self):
        """Notes are counted by the notes provider, not as generic files."""
        File.objects.create(
            owner=self.alice,
            name="My Note",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )

        stats = self.provider.get_stats(self.alice.id)

        self.assertEqual(stats["total_files"], 2)


class RecentFeedEventsTests(TestCase):
    """The feed reads a bounded set of candidate files per access arm."""

    def setUp(self):
        from django.contrib.auth.models import Group

        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="team")
        self.alice.groups.add(self.team)
        self.team_root = File.objects.create(
            owner=self.bob,
            name="team",
            node_type=File.NodeType.FOLDER,
            group=self.team,
        )
        self.base = timezone.now() - timedelta(days=1)
        self.minute = 0

    def _file(self, owner, name, group=None, mime_type="text/plain"):
        return File.objects.create(
            owner=owner,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type=mime_type,
            group=group,
            parent=self.team_root if group else None,
        )

    def _event(self, file_obj, actor):
        """Record an event one minute after the previous one, so the order is total."""
        self.minute += 1
        at = self.base + timedelta(minutes=self.minute)
        event = record_event(file_obj, actor, FileEvent.Action.CONTENT_REPLACED)
        FileEvent.objects.filter(pk=event.pk).update(created_at=at)
        File.objects.filter(pk=file_obj.pk).update(last_event_at=at)
        return event

    def _expected(self, viewer, limit, offset, exclude_actor_id=None):
        from workspace.files.services import FileService

        qs = FileEvent.objects.filter(
            file_id__in=FileService.accessible_file_ids(viewer),
            file__node_type=File.NodeType.FILE,
        ).exclude(file__mime_type="text/markdown")
        if exclude_actor_id is not None:
            qs = qs.exclude(actor_id=exclude_actor_id)
        return list(
            qs.order_by("-created_at").values_list("pk", flat=True)[
                offset : offset + limit
            ]
        )

    def _feed(self, viewer, limit, offset, exclude_actor_id=None):
        from workspace.files.models import FILE_FEED_Q
        from workspace.files.services.events import recent_feed_events

        events = recent_feed_events(
            FILE_FEED_Q,
            viewer=viewer,
            exclude_actor_id=exclude_actor_id,
            limit=limit,
            offset=offset,
        )
        return [e.pk for e in events]

    def test_pages_match_every_accessible_event_newest_first(self):
        own = [self._file(self.alice, f"own-{i}.txt") for i in range(6)]
        team = [
            self._file(self.bob, f"team-{i}.txt", group=self.team) for i in range(4)
        ]
        shared = [self._file(self.bob, f"shared-{i}.txt") for i in range(3)]
        private = [self._file(self.bob, f"private-{i}.txt") for i in range(3)]
        for f in shared:
            FileShare.objects.create(file=f, shared_by=self.bob, shared_with=self.alice)
        # One file collects a burst of the newest events, so a page can hold
        # several events of the same candidate.
        files = own + team + shared + private
        for round_ in range(3):
            for f in files:
                self._event(f, self.bob if round_ % 2 else self.alice)
        for _ in range(5):
            self._event(team[0], self.bob)

        pages = [(5, 0), (5, 5), (7, 12), (100, 0), (3, 40)]
        for exclude in (None, self.alice.id, self.bob.id):
            for limit, offset in pages:
                with self.subTest(limit=limit, offset=offset, exclude=exclude):
                    self.assertEqual(
                        self._feed(self.alice, limit, offset, exclude),
                        self._expected(self.alice, limit, offset, exclude),
                    )
        private_ids = {f.pk for f in private}
        visible = FileEvent.objects.filter(pk__in=self._feed(self.alice, 100, 0))
        self.assertFalse(visible.filter(file_id__in=private_ids).exists())

    def _bob_event_behind_alices_edits(self):
        """Bob's event on a team file, behind Alice's edits on 20 other team
        files Bob owns: ranked by newest event, those files hold none of the
        events a feed hiding Alice's own can show."""
        older = self._file(self.bob, "older.txt", group=self.team)
        bobs = self._event(older, self.bob)
        for i in range(20):
            self._event(
                self._file(self.bob, f"team-{i}.txt", group=self.team), self.alice
            )
        return bobs

    def test_widens_past_files_whose_newest_events_are_excluded(self):
        bobs = self._bob_event_behind_alices_edits()

        self.assertEqual(self._feed(self.alice, 3, 0, self.alice.id), [bobs.pk])

    def test_falls_back_to_the_accessible_set_past_the_candidate_cap(self):
        from unittest.mock import patch

        bobs = self._bob_event_behind_alices_edits()

        with patch("workspace.files.services.events._MAX_FEED_CANDIDATES", 4):
            self.assertEqual(self._feed(self.alice, 3, 0, self.alice.id), [bobs.pk])
            self.assertEqual(
                self._feed(self.alice, 5, 2), self._expected(self.alice, 5, 2)
            )

    def test_own_files_rank_by_their_newest_event_by_someone_else(self):
        """Alice's files whose newest events are all hers stay out of the
        dashboard read: one pass over the foreign ranking finds Bob's event."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        older = self._file(self.alice, "older.txt")
        bobs = self._event(older, self.bob)
        for i in range(20):
            self._event(self._file(self.alice, f"own-{i}.txt"), self.alice)
        self._event(older, self.alice)

        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self._feed(self.alice, 3, 0, self.alice.id), [bobs.pk])
        event_reads = [q for q in ctx.captured_queries if "files_fileevent" in q["sql"]]
        self.assertEqual(len(event_reads), 1)

    def test_moving_a_team_file_to_personal_keeps_its_owners_events_visible(self):
        """Bob's events on a team file become someone else's events once Alice
        moves the file into her own space: her dashboard must still show them."""
        from workspace.files.services import FileService

        doc = self._file(self.bob, "doc.txt", group=self.team)
        bobs = self._event(doc, self.bob)

        mine = File.objects.create(
            owner=self.alice, name="Mine", node_type=File.NodeType.FOLDER
        )
        FileService.move(doc, mine, acting_user=self.alice)

        self.assertEqual(self._feed(self.alice, 5, 0, self.alice.id), [bobs.pk])

    def test_feed_reads_a_bounded_set_of_files(self):
        """No query enumerates the files the viewer can access: every read of
        files_file stops at a LIMIT, so the cost follows the page size."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for i in range(12):
            own = self._file(self.alice, f"own-{i}.txt")
            self._event(own, self.alice)
            self._event(own, self.bob)
        self._event(self._file(self.bob, "team.txt", group=self.team), self.bob)

        # None is the profile read, Alice's own id the dashboard one.
        for exclude in (None, self.alice.id):
            with (
                self.subTest(exclude=exclude),
                CaptureQueriesContext(connection) as ctx,
            ):
                events = FilesActivityProvider().get_recent_events(
                    None, limit=3, viewer_id=self.alice.id, exclude_actor_id=exclude
                )

            self.assertEqual(len(events), 3)
            file_reads = [
                q["sql"] for q in ctx.captured_queries if '"files_file"' in q["sql"]
            ]
            self.assertTrue(file_reads)
            for sql in file_reads:
                self.assertNotIn("UNION", sql)
                self.assertIn("LIMIT", sql)
