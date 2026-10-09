from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, CalendarObjectChange, Event
from workspace.calendar.services.sync_log import (
    changes_since,
    object_etag,
    object_revisions,
)

User = get_user_model()

START = datetime(2026, 10, 5, 9, tzinfo=UTC)


class SyncLogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="syncer", password="pw")
        self.calendar = Calendar.objects.create(name="Work", owner=self.user)

    def _event(self, **fields):
        return Event.objects.create(
            calendar=fields.pop("calendar", self.calendar),
            owner=self.user,
            title=fields.pop("title", "Meeting"),
            start=START,
            **fields,
        )

    def _revision(self, calendar=None):
        calendar = calendar or self.calendar
        calendar.refresh_from_db(fields=["sync_revision"])
        return calendar.sync_revision

    def test_create_and_update_stamp_the_object(self):
        event = self._event()
        first = self._revision()
        self.assertEqual(object_revisions(self.calendar.pk), {event.pk: first})

        event.title = "Renamed"
        event.save(update_fields=["title"])

        self.assertGreater(self._revision(), first)
        self.assertEqual(
            object_revisions(self.calendar.pk), {event.pk: self._revision()}
        )
        self.assertNotEqual(
            object_etag(event.pk, first), object_etag(event.pk, self._revision())
        )

    def test_exception_row_stamps_its_master(self):
        master = self._event(recurrence_rule="RRULE:FREQ=DAILY")
        before = self._revision()

        exc = self._event(recurrence_parent=master, original_start=START)

        self.assertEqual(
            object_revisions(self.calendar.pk), {master.pk: self._revision()}
        )
        self.assertGreater(self._revision(), before)
        self.assertNotIn(exc.pk, object_revisions(self.calendar.pk))

    def test_deleted_object_stays_reported_by_name(self):
        event = self._event(dav_name="named.ics")
        event_uuid = event.pk
        token = self._revision()

        event.delete()

        (change,) = changes_since(self.calendar.pk, token)
        self.assertEqual(change.event_uuid, event_uuid)
        self.assertEqual(change.name, "named.ics")

    def test_deleting_a_series_reports_the_master_only(self):
        master = self._event(recurrence_rule="RRULE:FREQ=DAILY")
        self._event(recurrence_parent=master, original_start=START)
        token = self._revision()

        Event.objects.filter(pk=master.pk).delete()

        self.assertEqual(
            [change.event_uuid for change in changes_since(self.calendar.pk, token)],
            [master.pk],
        )

    def test_move_reports_the_object_gone_from_the_old_calendar(self):
        home = Calendar.objects.create(name="Home", owner=self.user)
        event = Event.objects.get(pk=self._event().pk)
        token = self._revision()

        event.calendar = home
        event.save()

        self.assertEqual(
            [change.event_uuid for change in changes_since(self.calendar.pk, token)],
            [event.pk],
        )
        self.assertIn(event.pk, object_revisions(home.pk))

    def test_deleting_the_calendar_records_nothing(self):
        self._event()
        self.calendar.delete()
        self.assertFalse(CalendarObjectChange.objects.exists())

    def test_deleting_the_owner_records_nothing(self):
        self._event()
        self.user.delete()
        self.assertFalse(CalendarObjectChange.objects.exists())
