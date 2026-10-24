"""CalDAV against a live server, driven by a third-party client.

The unit suite checks our reading of the RFCs; this one checks that somebody
else's reading agrees with it. The client is the ``caldav`` library, the one
most Python integrations (and several sync bridges) are built on, talking HTTP
to a real server thread: discovery, listing, both directions of a round trip,
time-range search, incremental sync and authentication.

The server runs the project's WSGI entry point rather than the bare Django
handler, so a client given only the server address goes through the same
routing as in production, where "/" belongs to the file WebDAV app.
"""

from datetime import UTC, datetime
from unittest import skipUnless

from caldav import DAVClient
from caldav.lib import error
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from knox.models import AuthToken

from workspace import wsgi
from workspace.calendar.models import Calendar, CalendarSubscription, Event
from workspace.calendar.models_external import ExternalCalendar
from workspace.common.dav.auth import clear_auth_cache
from workspace.common.tests.e2e.base import E2E_ENABLED

User = get_user_model()

PASSWORD = "alice-caldav-pw"

SERIES = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//E2E//EN
BEGIN:VEVENT
UID:e2e-series@example.com
DTSTAMP:20261001T080000Z
DTSTART;TZID=Europe/Paris:20261005T090000
DTEND;TZID=Europe/Paris:20261005T100000
SUMMARY:Weekly sync
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=8
CATEGORIES:Work
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT10M
DESCRIPTION:Soon
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:e2e-series@example.com
DTSTAMP:20261001T080000Z
RECURRENCE-ID;TZID=Europe/Paris:20261012T090000
DTSTART;TZID=Europe/Paris:20261012T140000
DTEND;TZID=Europe/Paris:20261012T150000
SUMMARY:Weekly sync (afternoon)
END:VEVENT
END:VCALENDAR
"""


@skipUnless(E2E_ENABLED, "E2E tests are disabled (set E2E=1 to run)")
class CalDavClientTests(StaticLiveServerTestCase):
    # Serve the project's WSGI router, not just Django: see the module docstring.
    static_handler = staticmethod(lambda _django_handler: wsgi.application)

    def setUp(self):
        clear_auth_cache()
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password=PASSWORD
        )
        self.calendar = Calendar.objects.create(name="Work", owner=self.user)

    def tearDown(self):
        clear_auth_cache()
        cache.clear()

    def client_for(self, path="/", username="alice", password=PASSWORD):
        client = DAVClient(
            url=f"{self.live_server_url}{path}",
            username=username,
            password=password,
            require_tls=False,
            timeout=30,
        )
        self.addCleanup(client.close)
        return client

    def work_calendar(self, client=None):
        calendars = (client or self.client_for()).principal().calendars()
        return next(c for c in calendars if c.get_display_name() == "Work")

    # ── Discovery ────────────────────────────────────────────────────

    def test_discovery_from_the_server_address(self):
        principal = self.client_for("/").principal()
        self.assertTrue(str(principal.url).endswith("/caldav/principals/alice/"))
        self.assertEqual(
            [c.get_display_name() for c in principal.calendars()], ["Work"]
        )

    def test_discovery_through_well_known(self):
        principal = self.client_for("/.well-known/caldav").principal()
        self.assertTrue(str(principal.url).endswith("/caldav/principals/alice/"))

    def test_home_lists_subscribed_and_external_calendars(self):
        bob = User.objects.create_user(username="bob", password="bob-pw-1")
        shared = Calendar.objects.create(name="Bob's", owner=bob)
        CalendarSubscription.objects.create(user=self.user, calendar=shared)
        feed = Calendar.objects.create(name="Holidays", owner=self.user)
        ExternalCalendar.objects.create(calendar=feed, url="https://example.com/h.ics")

        names = {
            c.get_display_name() for c in self.client_for().principal().calendars()
        }

        self.assertEqual(names, {"Work", "Bob's", "Holidays"})

    # ── Round trips ──────────────────────────────────────────────────

    def test_recurring_event_with_an_edited_occurrence_round_trips(self):
        calendar = self.work_calendar()
        calendar.add_event(SERIES)

        master = Event.objects.get(calendar=self.calendar, recurrence_parent=None)
        self.assertEqual(master.title, "Weekly sync")
        self.assertTrue(master.is_recurring)
        (moved,) = master.exceptions.all()
        self.assertEqual(moved.start, datetime(2026, 10, 12, 12, tzinfo=UTC))

        data = calendar.get_event_by_uid("e2e-series@example.com").data
        for expected in (
            "FREQ=WEEKLY",
            "BYDAY=MO",
            "COUNT=8",
            "RECURRENCE-ID;TZID=Europe/Paris:20261012T090000",
            "SUMMARY:Weekly sync (afternoon)",
            "CATEGORIES:Work",
            "TRIGGER:-PT10M",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, data)

    def test_event_created_in_the_app_reaches_the_client_and_comes_back_edited(self):
        native = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="From the web",
            start=datetime(2026, 10, 5, 9, tzinfo=UTC),
            end=datetime(2026, 10, 5, 10, tzinfo=UTC),
        )
        calendar = self.work_calendar()

        (event,) = calendar.get_events()
        component = event.icalendar_component
        self.assertEqual(str(component["SUMMARY"]), "From the web")

        component["SUMMARY"] = "From the phone"
        event.save()

        native.refresh_from_db()
        self.assertEqual(native.title, "From the phone")
        self.assertEqual(Event.objects.filter(calendar=self.calendar).count(), 1)

    def test_deleting_from_the_client_deletes_the_series(self):
        calendar = self.work_calendar()
        calendar.add_event(SERIES)

        calendar.get_event_by_uid("e2e-series@example.com").delete()

        self.assertFalse(Event.objects.filter(calendar=self.calendar).exists())

    def test_time_range_search(self):
        Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Later",
            start=datetime(2027, 3, 1, 9, tzinfo=UTC),
            end=datetime(2027, 3, 1, 10, tzinfo=UTC),
        )
        calendar = self.work_calendar()
        calendar.add_event(SERIES)

        # post_filter=False: the client would otherwise drop out-of-range
        # events itself and hide a server that ignores the time range.
        found = calendar.search(
            start=datetime(2026, 10, 1, tzinfo=UTC),
            end=datetime(2026, 11, 1, tzinfo=UTC),
            event=True,
            post_filter=False,
        )

        self.assertEqual(
            [str(e.icalendar_component["SUMMARY"]) for e in found], ["Weekly sync"]
        )

    # ── Incremental sync ─────────────────────────────────────────────

    # The client falls back to refetching the whole calendar when the server
    # does not answer sync-collection, and keeps a "fake-" token: a broken
    # REPORT would still pass these tests unless the fallback is ruled out.
    def synced_collection(self, calendar):
        return calendar.get_objects_by_sync_token(
            load_objects=True, disable_fallback=True
        )

    def sync(self, collection):
        result = collection.sync()
        self.assertFalse(str(collection.sync_token).startswith("fake-"))
        return result

    def test_sync_token_reports_changes_made_in_the_app(self):
        calendar = self.work_calendar()
        calendar.add_event(SERIES)
        kept = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Dentist",
            start=datetime(2027, 3, 1, 9, tzinfo=UTC),
        )
        collection = self.synced_collection(calendar)
        self.assertEqual(len(list(collection)), 2)

        kept.title = "Dentist (moved)"
        kept.save(update_fields=["title"])
        Event.objects.filter(ical_uid="e2e-series@example.com").delete()

        updated, deleted = collection.sync()

        self.assertEqual(
            [str(o.icalendar_component["SUMMARY"]) for o in updated],
            ["Dentist (moved)"],
        )
        self.assertEqual(len(deleted), 1)
        self.assertEqual([o.id for o in collection], [o.id for o in updated])

    def test_repeated_syncs_report_nothing_when_nothing_changed(self):
        calendar = self.work_calendar()
        calendar.add_event(SERIES)
        collection = self.synced_collection(calendar)

        for _ in range(3):
            updated, deleted = self.sync(collection)
            self.assertEqual((list(updated), list(deleted)), ([], []))
        self.assertEqual(Event.objects.filter(recurrence_parent=None).count(), 1)

    # ── Authentication and rights ────────────────────────────────────

    def test_wrong_password_is_refused(self):
        with self.assertRaises(error.AuthorizationError):
            self.client_for(password="wrong").principal()

    def test_api_token_authenticates_and_revoking_it_stops_syncing(self):
        instance, token = AuthToken.objects.create(self.user)
        self.assertEqual(
            self.work_calendar(self.client_for(password=token)).get_display_name(),
            "Work",
        )

        instance.delete()
        clear_auth_cache()  # what the cache TTL does on its own

        with self.assertRaises(error.AuthorizationError):
            self.client_for(password=token).principal()

    def test_subscribed_calendar_is_read_only(self):
        bob = User.objects.create_user(username="bob", password="bob-pw-1")
        shared = Calendar.objects.create(name="Bob's", owner=bob)
        CalendarSubscription.objects.create(user=self.user, calendar=shared)
        calendars = self.client_for().principal().calendars()
        theirs = next(c for c in calendars if c.get_display_name() == "Bob's")

        with self.assertRaises(error.DAVError):
            theirs.add_event(SERIES)

        self.assertFalse(Event.objects.filter(calendar=shared).exists())
