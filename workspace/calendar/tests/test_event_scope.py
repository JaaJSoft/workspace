from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services.event_scope import cancel_event, update_event
from workspace.calendar.services.recurrence_rule import apply_rule

User = get_user_model()

PARIS = "Europe/Paris"


class ScopedEditStorageInvariantsTests(TestCase):
    """The derived rows a scoped edit writes must respect the storage contract."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.calendar = Calendar.objects.create(name="Work", owner=self.user)

    def _weekly(self, **kwargs):
        defaults = {
            "calendar": self.calendar,
            "owner": self.user,
            "title": "Weekly",
            # A March start: the third occurrence lands after the European DST
            # switch on the last Sunday of the month.
            "start": datetime(2026, 3, 16, 9, 0, tzinfo=UTC),
            "end": datetime(2026, 3, 16, 10, 0, tzinfo=UTC),
        }
        event = Event(**{**defaults, **kwargs})
        apply_rule(event, "RRULE:FREQ=WEEKLY;INTERVAL=1")
        event.save()
        return event

    def test_future_split_carries_the_series_timezone(self):
        master = self._weekly(timezone=PARIS)
        third = master.start + timedelta(weeks=2)

        new_master = update_event(
            master,
            {"title": "Renamed"},
            self.user,
            scope="future",
            original_start=third,
        )

        # A blank timezone selects the legacy fixed-step UTC expansion, which
        # walks the local wall clock by an hour across the DST boundary.
        self.assertEqual(new_master.timezone, PARIS)

    def test_future_split_of_an_all_day_series_stays_zone_less(self):
        master = self._weekly(
            all_day=True,
            start=datetime(2026, 3, 16, tzinfo=UTC),
            end=None,
            timezone="",
        )
        third = master.start + timedelta(weeks=2)

        new_master = update_event(
            master,
            {"title": "Renamed"},
            self.user,
            scope="future",
            original_start=third,
        )

        self.assertEqual(new_master.timezone, "")

    def test_exception_on_an_all_day_series_is_normalized_to_midnight(self):
        master = self._weekly(
            all_day=True,
            start=datetime(2026, 3, 16, tzinfo=UTC),
            end=None,
            timezone="",
        )
        second = master.start + timedelta(weeks=1)

        # all_day is inherited from the master, so a payload that only moves
        # the start never goes through the serializer's normalization.
        exc = update_event(
            master,
            {"start": datetime(2026, 3, 24, 14, 30, tzinfo=UTC)},
            self.user,
            scope="this",
            original_start=second,
        )

        self.assertTrue(exc.all_day)
        self.assertEqual(exc.start, datetime(2026, 3, 24, tzinfo=UTC))

    def test_future_split_of_an_all_day_series_normalizes_a_moved_start(self):
        master = self._weekly(
            all_day=True,
            start=datetime(2026, 3, 16, tzinfo=UTC),
            end=None,
            timezone="",
        )
        third = master.start + timedelta(weeks=2)

        new_master = update_event(
            master,
            {"start": datetime(2026, 3, 31, 8, 15, tzinfo=UTC)},
            self.user,
            scope="future",
            original_start=third,
        )

        self.assertEqual(new_master.start, datetime(2026, 3, 31, tzinfo=UTC))

    def test_cancelling_one_occurrence_leaves_the_series_alone(self):
        master = self._weekly()
        second = master.start + timedelta(weeks=1)

        cancel_event(master, self.user, scope="this", original_start=second)

        master.refresh_from_db()
        self.assertIsNone(master.recurrence_until)
        self.assertEqual(
            Event.objects.filter(recurrence_parent=master, is_cancelled=True).count(), 1
        )

    def test_single_occurrence_exception_keeps_the_master_owner(self):
        # workspace.chat.services.meetings.create_meeting redirects an
        # exception to its recurrence_parent and trusts that row's owner
        # check to also cover the exception - that only holds because the
        # exception always inherits the master's owner, never the acting
        # editor's. update_event itself does not gate on ownership (the
        # view and the AI tool do, before calling in), so this is provable
        # with an editor who differs from the owner.
        master = self._weekly()
        editor = User.objects.create_user(username="editor", password="pw")
        second = master.start + timedelta(weeks=1)

        exc = update_event(
            master, {"title": "Moved"}, editor, scope="this", original_start=second
        )

        self.assertEqual(exc.owner_id, master.owner_id)
        self.assertNotEqual(exc.owner_id, editor.id)

    def test_cancelling_one_occurrence_keeps_the_master_owner(self):
        master = self._weekly()
        editor = User.objects.create_user(username="canceleditor", password="pw")
        second = master.start + timedelta(weeks=1)

        cancel_event(master, editor, scope="this", original_start=second)

        exc = Event.objects.get(recurrence_parent=master, is_cancelled=True)
        self.assertEqual(exc.owner_id, master.owner_id)
        self.assertNotEqual(exc.owner_id, editor.id)
