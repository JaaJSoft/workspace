from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File, FileEvent
from workspace.files.services.events import (
    events_for_file,
    record_event,
    serialize_event,
)

User = get_user_model()


class RecordEventTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="alice@test.com",
            password="pass123",
        )
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

    def test_record_event_persists_action_and_actor(self):
        event = record_event(
            self.file, self.user, FileEvent.Action.RENAMED, {"old": "a", "new": "b"}
        )

        self.assertIsNotNone(event)
        stored = FileEvent.objects.get(pk=event.pk)
        self.assertEqual(stored.file, self.file)
        self.assertEqual(stored.actor, self.user)
        self.assertEqual(stored.action, "renamed")
        self.assertEqual(stored.metadata, {"old": "a", "new": "b"})

    def test_record_event_with_no_metadata_defaults_to_empty_dict(self):
        event = record_event(self.file, self.user, FileEvent.Action.CREATED)

        self.assertIsNotNone(event)
        self.assertEqual(event.metadata, {})

    def test_record_event_actor_none_for_anonymous_user(self):
        from django.contrib.auth.models import AnonymousUser

        event = record_event(self.file, AnonymousUser(), FileEvent.Action.SHARED)

        self.assertIsNotNone(event)
        self.assertIsNone(event.actor)

    def test_record_event_actor_none_for_system_action(self):
        event = record_event(self.file, None, FileEvent.Action.DELETED)

        self.assertIsNotNone(event)
        self.assertIsNone(event.actor)

    def test_record_event_returns_none_for_missing_file(self):
        self.assertIsNone(record_event(None, self.user, FileEvent.Action.CREATED))

    def test_record_event_skips_unsaved_file_instance(self):
        # Defensive guard: tests that mock FileService.create_* return a
        # non-persisted File instance. Recording an event against it would
        # blow up the test transaction with a deferred-FK violation.
        unsaved = File(
            owner=self.user,
            name="ghost.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        # Sanity: a freshly constructed instance has _state.adding == True.
        self.assertTrue(unsaved._state.adding)

        result = record_event(unsaved, self.user, FileEvent.Action.CREATED)

        self.assertIsNone(result)
        self.assertEqual(FileEvent.objects.count(), 0)

    def test_record_event_stamps_last_event_at_on_the_file_row(self):
        event = record_event(self.file, self.user, FileEvent.Action.RENAMED)

        self.file.refresh_from_db(fields=["last_event_at"])
        self.assertEqual(self.file.last_event_at, event.created_at)

    def test_record_event_never_moves_last_event_at_backwards(self):
        later = timezone.now() + timedelta(days=1)
        File.objects.filter(pk=self.file.pk).update(last_event_at=later)

        record_event(self.file, self.user, FileEvent.Action.RENAMED)

        self.file.refresh_from_db(fields=["last_event_at"])
        self.assertEqual(self.file.last_event_at, later)

    def test_owner_events_leave_last_foreign_event_at_alone(self):
        record_event(self.file, self.user, FileEvent.Action.RENAMED)

        self.file.refresh_from_db(fields=["last_foreign_event_at"])
        self.assertIsNone(self.file.last_foreign_event_at)

    def test_other_and_system_events_stamp_last_foreign_event_at(self):
        bob = User.objects.create_user(username="bob", password="pass123")
        for actor in (bob, None):
            with self.subTest(actor=actor):
                event = record_event(self.file, actor, FileEvent.Action.RENAMED)

                self.file.refresh_from_db(fields=["last_foreign_event_at"])
                self.assertEqual(self.file.last_foreign_event_at, event.created_at)

    def test_a_bare_create_stamps_the_file_row_too(self):
        """Writers that skip record_event still keep the feed columns."""
        event = FileEvent.objects.create(
            file=self.file, actor=None, action=FileEvent.Action.DELETED
        )

        self.file.refresh_from_db(fields=["last_event_at", "last_foreign_event_at"])
        self.assertEqual(self.file.last_event_at, event.created_at)
        self.assertEqual(self.file.last_foreign_event_at, event.created_at)

    def test_record_event_updates_the_in_memory_instance(self):
        # A later full save() of the same instance must not write NULL back.
        event = record_event(self.file, self.user, FileEvent.Action.RENAMED)

        self.assertEqual(self.file.last_event_at, event.created_at)


class EventsForFileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="alice@test.com",
            password="pass123",
        )
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        self.other_file = File.objects.create(
            owner=self.user,
            name="other.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

    def test_returns_only_events_for_target_file(self):
        record_event(self.file, self.user, FileEvent.Action.CREATED)
        record_event(self.file, self.user, FileEvent.Action.RENAMED)
        record_event(self.other_file, self.user, FileEvent.Action.CREATED)

        events = list(events_for_file(self.file))

        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.file == self.file for e in events))

    def test_returns_events_newest_first(self):
        first = record_event(self.file, self.user, FileEvent.Action.CREATED)
        second = record_event(self.file, self.user, FileEvent.Action.RENAMED)
        third = record_event(self.file, self.user, FileEvent.Action.SHARED)

        events = list(events_for_file(self.file))

        self.assertEqual([e.pk for e in events], [third.pk, second.pk, first.pk])


class SerializeEventTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            first_name="Alice",
            last_name="Anderson",
            email="alice@test.com",
            password="pass123",
        )
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

    def test_serialize_event_includes_human_label_and_icon(self):
        event = record_event(
            self.file, self.user, FileEvent.Action.RENAMED, {"old": "a", "new": "b"}
        )

        data = serialize_event(event)

        self.assertEqual(data["action"], "renamed")
        self.assertEqual(data["label"], "Renamed")
        self.assertEqual(data["icon"], "pencil")
        self.assertEqual(data["metadata"], {"old": "a", "new": "b"})
        self.assertEqual(data["actor"]["username"], "alice")
        self.assertEqual(data["actor"]["full_name"], "Alice Anderson")
        self.assertIn("created_at", data)
        self.assertIn("uuid", data)

    def test_serialize_event_handles_null_actor(self):
        event = record_event(self.file, None, FileEvent.Action.DELETED)

        data = serialize_event(event)

        self.assertIsNone(data["actor"])
        self.assertEqual(data["action"], "deleted")

    def test_serialize_event_falls_back_to_username_when_full_name_empty(self):
        plain_user = User.objects.create_user(
            username="nooneknows",
            email="n@test.com",
            password="pass",
        )
        event = record_event(self.file, plain_user, FileEvent.Action.SHARED)

        data = serialize_event(event)

        self.assertEqual(data["actor"]["full_name"], "nooneknows")


class LastEventBackfillTests(TestCase):
    """files.0061 fills both feed columns from the existing events."""

    def test_backfill_ranks_owner_and_foreign_events(self):
        import importlib
        from types import SimpleNamespace

        from django.apps import apps
        from django.db import connection

        alice = User.objects.create_user(username="alice", password="pass123")
        quiet, owned, shared = (
            File.objects.create(owner=alice, name=n, node_type=File.NodeType.FILE)
            for n in ("quiet.txt", "owned.txt", "shared.txt")
        )
        record_event(owned, alice, FileEvent.Action.CREATED)
        foreign = record_event(shared, None, FileEvent.Action.CREATED)
        newest = record_event(shared, alice, FileEvent.Action.RENAMED)
        File.objects.update(last_event_at=None, last_foreign_event_at=None)

        migration = importlib.import_module(
            "workspace.files.migrations.0061_file_last_event_at"
        )
        migration.backfill_last_event_at(apps, SimpleNamespace(connection=connection))

        rows = {
            f.name: (f.last_event_at, f.last_foreign_event_at)
            for f in File.objects.all()
        }
        self.assertEqual(rows["quiet.txt"], (None, None))
        self.assertEqual(rows["owned.txt"][1], None)
        self.assertIsNotNone(rows["owned.txt"][0])
        self.assertEqual(rows["shared.txt"], (newest.created_at, foreign.created_at))
