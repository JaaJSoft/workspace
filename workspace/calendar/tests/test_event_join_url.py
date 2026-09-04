import json

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.calendar.models import Calendar, Event

User = get_user_model()


class EventJoinUrlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", "owner@example.com", "pw")
        self.calendar = Calendar.objects.create(owner=self.user, name="Cal")
        self.event = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Sync",
            start=timezone.now() + timezone.timedelta(hours=1),
            end=timezone.now() + timezone.timedelta(hours=2),
        )
        self.client.force_login(self.user)

    def _query_count(self, url):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(url)
        # A guard measuring queries on a failed request (a 400/500 short
        # circuit before the listing even runs) would report a bogus low
        # count and pass for the wrong reason.
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries)

    def test_join_url_is_null_without_a_meeting(self):
        resp = self.client.get(f"/api/v1/events/{self.event.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["join_url"])

    def test_join_url_is_absolute_once_a_meeting_exists(self):
        from workspace.chat.services.meetings import create_meeting

        meeting = create_meeting(self.event, self.user)
        resp = self.client.get(f"/api/v1/events/{self.event.uuid}")
        self.assertEqual(
            resp.json()["join_url"], f"http://testserver/meet/{meeting.slug}"
        )

    def test_listing_events_does_not_query_per_event_for_the_meeting(self):
        from workspace.chat.services.meetings import create_meeting

        create_meeting(self.event, self.user)
        start = (timezone.now() - timezone.timedelta(days=1)).isoformat()
        end = (timezone.now() + timezone.timedelta(days=1)).isoformat()
        url = f"/api/v1/events?start={start}&end={end}"

        # Measured against a single meeting-bearing event; growing the
        # dataset below must not grow this count (the meeting is
        # select_related, not queried per event).
        baseline = self._query_count(url)

        for i in range(5):
            extra = Event.objects.create(
                calendar=self.calendar,
                owner=self.user,
                title=f"E{i}",
                start=timezone.now() + timezone.timedelta(hours=3 + i),
                end=timezone.now() + timezone.timedelta(hours=4 + i),
            )
            if i == 0:
                create_meeting(extra, self.user)

        with self.assertNumQueries(baseline):
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_recurring_occurrences_carry_the_absolute_join_url(self):
        from workspace.chat.services.meetings import create_meeting

        master = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Standup",
            start=timezone.now() + timezone.timedelta(hours=1),
            end=timezone.now() + timezone.timedelta(hours=2),
            recurrence_frequency="daily",
        )
        meeting = create_meeting(master, self.user)
        start = timezone.now().isoformat()
        end = (timezone.now() + timezone.timedelta(days=3)).isoformat()
        resp = self.client.get(f"/api/v1/events?start={start}&end={end}")
        occurrences = [e for e in resp.json() if e["title"] == "Standup"]
        self.assertGreaterEqual(len(occurrences), 3)
        for occ in occurrences:
            self.assertEqual(occ["join_url"], f"http://testserver/meet/{meeting.slug}")

    def test_recurring_occurrences_are_null_without_a_meeting(self):
        Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Standup",
            start=timezone.now() + timezone.timedelta(hours=1),
            end=timezone.now() + timezone.timedelta(hours=2),
            recurrence_frequency="daily",
        )
        start = timezone.now().isoformat()
        end = (timezone.now() + timezone.timedelta(days=3)).isoformat()
        resp = self.client.get(f"/api/v1/events?start={start}&end={end}")
        occurrences = [e for e in resp.json() if e["title"] == "Standup"]
        self.assertGreaterEqual(len(occurrences), 3)
        for occ in occurrences:
            self.assertIsNone(occ["join_url"])

    def _create_recurring_series_with_exception(self, title, with_meeting):
        from workspace.chat.services.meetings import create_meeting

        master = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title=title,
            start=timezone.now() + timezone.timedelta(hours=1),
            end=timezone.now() + timezone.timedelta(hours=2),
            recurrence_frequency="daily",
        )
        if with_meeting:
            create_meeting(master, self.user)
        occ_start = master.start + timezone.timedelta(days=1)
        exception = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title=f"{title} (moved)",
            start=occ_start + timezone.timedelta(minutes=30),
            end=occ_start + timezone.timedelta(minutes=90),
            recurrence_parent=master,
            original_start=occ_start,
        )
        return master, exception

    def test_recurring_listing_does_not_query_per_occurrence_for_the_meeting(self):
        self._create_recurring_series_with_exception("Standup", with_meeting=True)
        start = timezone.now().isoformat()
        end = (timezone.now() + timezone.timedelta(days=3)).isoformat()
        url = f"/api/v1/events?start={start}&end={end}"

        # Measured against one recurring series (one meeting, one
        # materialized exception); adding more series below - each with its
        # own exception, two more with a meeting - must not grow this count
        # (the exception's meeting is reached through
        # recurrence_parent__meeting, select_related, not queried per
        # occurrence).
        baseline = self._query_count(url)

        for i in range(3):
            self._create_recurring_series_with_exception(
                f"Series{i}", with_meeting=(i < 2)
            )

        with self.assertNumQueries(baseline):
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_upcoming_listing_carries_the_absolute_join_url(self):
        from workspace.chat.services.meetings import create_meeting

        meeting = create_meeting(self.event, self.user)
        after = timezone.now().isoformat()
        resp = self.client.get(f"/api/v1/events?after={after}&limit=20")
        events = resp.json()["events"]
        matching = [e for e in events if e["uuid"] == str(self.event.uuid)]
        self.assertEqual(len(matching), 1)
        self.assertEqual(
            matching[0]["join_url"], f"http://testserver/meet/{meeting.slug}"
        )

    def test_upcoming_listing_does_not_query_per_event_for_the_meeting(self):
        from workspace.chat.services.meetings import create_meeting

        create_meeting(self.event, self.user)
        after = timezone.now().isoformat()
        url = f"/api/v1/events?after={after}&limit=20"

        # Same shape as test_listing_events_does_not_query_per_event_for_the_meeting:
        # measured against one meeting-bearing event, then the dataset grows.
        baseline = self._query_count(url)

        for i in range(5):
            extra = Event.objects.create(
                calendar=self.calendar,
                owner=self.user,
                title=f"E{i}",
                start=timezone.now() + timezone.timedelta(hours=3 + i),
                end=timezone.now() + timezone.timedelta(hours=4 + i),
            )
            if i == 0:
                create_meeting(extra, self.user)

        with self.assertNumQueries(baseline):
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_join_url_for_an_exception_detail_reads_the_series_master(self):
        # The detail GET is fetched by whatever uuid the calendar grid
        # handed it, which for a materialized exception is the exception's
        # own row - but the Meeting lives on the series master.
        master, exception = self._create_recurring_series_with_exception(
            "Standup", with_meeting=True
        )
        from workspace.chat.models import Meeting

        meeting = Meeting.objects.get(event=master)
        resp = self.client.get(f"/api/v1/events/{exception.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["join_url"], f"http://testserver/meet/{meeting.slug}"
        )

    def test_upcoming_listing_carries_the_absolute_join_url_on_an_exception(self):
        # upcoming.py's cursor mode deliberately includes materialized
        # exceptions as one-off events (they are real, addressable rows).
        master, exception = self._create_recurring_series_with_exception(
            "Standup", with_meeting=True
        )
        from workspace.chat.models import Meeting

        meeting = Meeting.objects.get(event=master)
        after = timezone.now().isoformat()
        resp = self.client.get(f"/api/v1/events?after={after}&limit=20")
        events = resp.json()["events"]
        matching = [e for e in events if e["uuid"] == str(exception.uuid)]
        self.assertEqual(len(matching), 1)
        self.assertEqual(
            matching[0]["join_url"], f"http://testserver/meet/{meeting.slug}"
        )

    def test_put_scope_this_returns_the_series_join_url(self):
        # calendar_events.js's saveEvent() copies the PUT response straight
        # into _panelRaw (line ~680), so a missing join_url here means the
        # open panel loses its link the moment a "this occurrence" edit
        # lands, until a reload re-fetches it correctly.
        from workspace.chat.services.meetings import create_meeting

        master = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Standup",
            start=timezone.now() + timezone.timedelta(hours=1),
            end=timezone.now() + timezone.timedelta(hours=2),
            recurrence_frequency="weekly",
        )
        meeting = create_meeting(master, self.user)
        occ_start = (master.start + timezone.timedelta(weeks=1)).isoformat()
        resp = self.client.put(
            f"/api/v1/events/{master.uuid}",
            data=json.dumps(
                {"scope": "this", "original_start": occ_start, "title": "Modified"}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["join_url"], f"http://testserver/meet/{meeting.slug}"
        )
