from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.ai_tools import (
    CalendarToolProvider,
    CreateEventParams,
    ListUpcomingEventsParams,
)
from workspace.calendar.models import Calendar, Event
from workspace.calendar.models_external import ExternalCalendar

User = get_user_model()


class CalendarAiToolsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.provider = CalendarToolProvider()

    def tearDown(self):
        cache.clear()

    def test_list_calendars_returns_owned_excludes_external(self):
        Calendar.objects.create(name="Perso", owner=self.user)
        ext = Calendar.objects.create(name="Holidays", owner=self.user)
        ExternalCalendar.objects.create(
            calendar=ext, url="https://example.com/feed.ics"
        )
        result = self.provider.list_calendars(
            {}, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("Perso", result)
        self.assertNotIn("Holidays", result)

    def test_list_upcoming_events_returns_future_within_window(self):
        cal = Calendar.objects.create(name="Work", owner=self.user)
        now = timezone.now()
        Event.objects.create(
            calendar=cal,
            owner=self.user,
            title="Soon",
            start=now + timedelta(days=1),
        )
        Event.objects.create(
            calendar=cal,
            owner=self.user,
            title="Later",
            start=now + timedelta(days=30),
        )
        args = ListUpcomingEventsParams(days_ahead=7, limit=20)
        result = self.provider.list_upcoming_events(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("Soon", result)
        self.assertNotIn("Later", result)

    def test_list_upcoming_events_excludes_other_users(self):
        other = User.objects.create_user(username="bob", password="pw")
        cal = Calendar.objects.create(name="BobCal", owner=other)
        Event.objects.create(
            calendar=cal,
            owner=other,
            title="BobSecret",
            start=timezone.now() + timedelta(days=1),
        )
        args = ListUpcomingEventsParams()
        result = self.provider.list_upcoming_events(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertNotIn("BobSecret", result)

    def _future_iso(self, **delta):
        return (timezone.now() + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M")

    def test_create_event_writes_to_first_owned_calendar(self):
        Calendar.objects.create(name="Perso", owner=self.user)
        args = CreateEventParams(title="Dentist", start=self._future_iso(days=1))
        result = self.provider.create_event(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("Created event", result)
        ev = Event.objects.get(title="Dentist")
        self.assertEqual(ev.owner, self.user)
        self.assertEqual(ev.calendar.name, "Perso")

    def test_create_event_auto_creates_calendar_when_none(self):
        args = CreateEventParams(title="Solo", start=self._future_iso(days=1))
        self.provider.create_event(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertTrue(Calendar.objects.filter(owner=self.user, name="Perso").exists())
        self.assertTrue(Event.objects.filter(title="Solo").exists())

    def test_create_event_rejects_past_start(self):
        Calendar.objects.create(name="Perso", owner=self.user)
        args = CreateEventParams(
            title="Past",
            start=(timezone.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        )
        result = self.provider.create_event(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("future", result)
        self.assertFalse(Event.objects.filter(title="Past").exists())

    def test_create_event_routes_by_calendar_name(self):
        Calendar.objects.create(name="Perso", owner=self.user)
        Calendar.objects.create(name="Boulot", owner=self.user)
        args = CreateEventParams(
            title="Standup", start=self._future_iso(days=1), calendar="boulot"
        )
        self.provider.create_event(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        ev = Event.objects.get(title="Standup")
        self.assertEqual(ev.calendar.name, "Boulot")

    def test_create_event_unknown_calendar_errors(self):
        Calendar.objects.create(name="Perso", owner=self.user)
        args = CreateEventParams(
            title="X", start=self._future_iso(days=1), calendar="Nope"
        )
        result = self.provider.create_event(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("no calendar named", result)
        self.assertFalse(Event.objects.filter(title="X").exists())
