from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services.calendar_objects import (
    calendar_masters,
    find_object,
    masters_in_range,
    store_object,
)
from workspace.calendar.services.ical_objects import (
    InvalidObject,
    parse_object,
    render_object,
)

from .test_ical_objects import OVERRIDE, SERIES, _ics

User = get_user_model()


class StoreObjectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="caldav", password="pw")
        cls.calendar = Calendar.objects.create(name="Work", owner=cls.user)

    def _store(self, body, name="series.ics", existing=None):
        return store_object(self.calendar, name, parse_object(body), existing)

    def test_create_series_with_override(self):
        master = self._store(_ics(SERIES, OVERRIDE))

        self.assertEqual(master.dav_name, "series.ics")
        self.assertEqual(master.ical_uid, "series-1")
        self.assertEqual(master.owner, self.user)
        self.assertEqual(master.source, Event.Source.CALDAV)
        self.assertTrue(master.is_recurring)
        (exc,) = master.exceptions.all()
        self.assertEqual(exc.original_start, datetime(2026, 10, 12, 7, tzinfo=UTC))
        self.assertEqual(exc.title, "Standup (moved)")
        self.assertIsNone(exc.ical_uid)

    def test_series_with_edited_occurrence_survives_a_round_trip(self):
        """What a client PUTs is what it GETs back, line for line where it matters."""
        sent = parse_object(_ics(SERIES, OVERRIDE))
        master = store_object(self.calendar, "series.ics", sent)

        served = parse_object(render_object(master, list(master.exceptions.all())))

        self.assertEqual(served.uid, sent.uid)
        for field in ("title", "start", "end", "timezone", "all_day", "rule", "extra"):
            with self.subTest(field=field):
                self.assertEqual(
                    getattr(served.master, field), getattr(sent.master, field)
                )
        self.assertEqual(len(served.overrides), 1)
        for field in ("recurrence_id", "title", "start", "end"):
            with self.subTest(override_field=field):
                self.assertEqual(
                    getattr(served.overrides[0], field),
                    getattr(sent.overrides[0], field),
                )

    def test_update_replaces_the_overrides(self):
        master = self._store(_ics(SERIES, OVERRIDE))
        renamed = [line.replace("Standup\\, daily", "Daily") for line in SERIES]

        again = self._store(_ics(renamed), existing=master)

        self.assertEqual(again.pk, master.pk)
        self.assertEqual(again.title, "Daily")
        self.assertFalse(Event.objects.filter(recurrence_parent=master).exists())

    def test_update_keeps_an_override_row(self):
        master = self._store(_ics(SERIES, OVERRIDE))
        exc_pk = master.exceptions.get().pk
        retitled = [line.replace("(moved)", "(again)") for line in OVERRIDE]

        self._store(_ics(SERIES, retitled), existing=master)

        exc = Event.objects.get(recurrence_parent=master)
        self.assertEqual(exc.pk, exc_pk)
        self.assertEqual(exc.title, "Standup (again)")

    def test_uid_cannot_change(self):
        master = self._store(_ics(SERIES))
        other = [line.replace("series-1", "series-2") for line in SERIES]
        with self.assertRaises(InvalidObject) as ctx:
            self._store(_ics(other), existing=master)
        self.assertEqual(ctx.exception.condition, "no-uid-conflict")

    def test_uid_already_in_the_calendar_is_a_conflict(self):
        self._store(_ics(SERIES))
        with self.assertRaises(InvalidObject) as ctx:
            self._store(_ics(SERIES), name="copy.ics")
        self.assertEqual(ctx.exception.condition, "no-uid-conflict")

    def test_native_event_keeps_its_uid_when_a_client_writes_it(self):
        native = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="From the web",
            start=datetime(2026, 10, 5, 9, tzinfo=UTC),
        )
        body = render_object(native).replace("From the web", "From the phone")

        stored = self._store(body, name=f"{native.uuid}.ics", existing=native)

        stored.refresh_from_db()
        self.assertEqual(stored.title, "From the phone")
        self.assertIsNone(stored.ical_uid)
        self.assertEqual(stored.dav_name, "")


class FindObjectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(username="finder", password="pw")
        cls.calendar = Calendar.objects.create(name="Work", owner=user)
        cls.other = Calendar.objects.create(name="Home", owner=user)
        start = datetime(2026, 10, 5, 9, tzinfo=UTC)
        cls.native = Event.objects.create(
            calendar=cls.calendar, owner=user, title="n", start=start
        )
        cls.named = Event.objects.create(
            calendar=cls.calendar,
            owner=user,
            title="d",
            start=start,
            dav_name="abc.ics",
        )

    def test_by_uuid_and_by_name(self):
        self.assertEqual(
            find_object(self.calendar, f"{self.native.uuid}.ics"), self.native
        )
        self.assertEqual(find_object(self.calendar, "abc.ics"), self.named)

    def test_misses(self):
        self.assertIsNone(find_object(self.other, "abc.ics"))
        self.assertIsNone(find_object(self.calendar, f"{self.named.uuid}.ics"))
        self.assertIsNone(find_object(self.calendar, "not-a-uuid.ics"))


class MastersInRangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(username="ranger", password="pw")
        cls.calendar = Calendar.objects.create(name="Work", owner=user)
        cls.inside = Event.objects.create(
            calendar=cls.calendar,
            owner=user,
            title="inside",
            start=datetime(2026, 10, 5, 9, tzinfo=UTC),
            end=datetime(2026, 10, 5, 10, tzinfo=UTC),
        )
        cls.before = Event.objects.create(
            calendar=cls.calendar,
            owner=user,
            title="before",
            start=datetime(2026, 9, 1, 9, tzinfo=UTC),
            end=datetime(2026, 9, 1, 10, tzinfo=UTC),
        )
        cls.series = store_object(
            cls.calendar, "series.ics", parse_object(_ics(SERIES))
        )

    def test_window(self):
        masters = masters_in_range(
            calendar_masters(self.calendar),
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 11, 1, tzinfo=UTC),
        )
        self.assertCountEqual(masters, [self.inside, self.series])

    def test_series_past_its_last_occurrence_is_out(self):
        masters = masters_in_range(
            calendar_masters(self.calendar), datetime(2027, 6, 1, tzinfo=UTC), None
        )
        self.assertEqual(list(masters), [])
