from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import (
    Calendar,
    CalendarSubscription,
    Event,
    EventMember,
)
from workspace.calendar.models_external import ExternalCalendar
from workspace.calendar.queries import (
    visible_calendar_ids,
    visible_calendars,
    visible_events_q,
)

User = get_user_model()


class CalendarAuthzMixin:
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')
        self.alice_cal = Calendar.objects.create(name='Alice Cal', owner=self.alice)
        self.bob_cal = Calendar.objects.create(name='Bob Cal', owner=self.bob)


# ── visible_calendar_ids ────────────────────────────────────────

class VisibleCalendarIdsTests(CalendarAuthzMixin, TestCase):

    def test_includes_owned_calendars(self):
        ids = visible_calendar_ids(self.alice)
        self.assertIn(self.alice_cal.pk, ids)

    def test_excludes_other_users_calendars(self):
        ids = visible_calendar_ids(self.alice)
        self.assertNotIn(self.bob_cal.pk, ids)

    def test_includes_subscribed_calendars(self):
        CalendarSubscription.objects.create(user=self.alice, calendar=self.bob_cal)
        ids = visible_calendar_ids(self.alice)
        self.assertIn(self.bob_cal.pk, ids)

    def test_includes_external_calendars(self):
        ext_cal = Calendar.objects.create(name='External', owner=self.alice)
        ExternalCalendar.objects.create(calendar=ext_cal, url='https://example.com/cal.ics')
        ids = visible_calendar_ids(self.alice)
        self.assertIn(ext_cal.pk, ids)

    def test_empty_for_user_with_no_calendars(self):
        carol = User.objects.create_user(username='carol', password='pass')
        self.assertEqual(visible_calendar_ids(carol), [])


# ── visible_calendars ──────────────────────────────────────────

class VisibleCalendarsTests(CalendarAuthzMixin, TestCase):

    def test_owned_includes_own_calendars(self):
        owned, _ = visible_calendars(self.alice)
        self.assertIn(self.alice_cal, list(owned))

    def test_owned_excludes_external_calendars(self):
        ext_cal = Calendar.objects.create(name='External', owner=self.alice)
        ExternalCalendar.objects.create(calendar=ext_cal, url='https://example.com/cal.ics')
        owned, _ = visible_calendars(self.alice)
        pks = [c.pk for c in owned]
        self.assertNotIn(ext_cal.pk, pks)

    def test_owned_excludes_other_users(self):
        owned, _ = visible_calendars(self.alice)
        pks = [c.pk for c in owned]
        self.assertNotIn(self.bob_cal.pk, pks)

    def test_subscribed_includes_subscribed_calendars(self):
        CalendarSubscription.objects.create(user=self.alice, calendar=self.bob_cal)
        _, subscribed = visible_calendars(self.alice)
        self.assertIn(self.bob_cal, list(subscribed))

    def test_subscribed_empty_when_no_subscriptions(self):
        _, subscribed = visible_calendars(self.alice)
        self.assertEqual(list(subscribed), [])


# ── visible_events_q ───────────────────────────────────────────

class VisibleEventsQTests(CalendarAuthzMixin, TestCase):

    def _make_event(self, calendar, title='Test Event', owner=None):
        return Event.objects.create(
            calendar=calendar,
            title=title,
            start=timezone.now(),
            owner=owner or calendar.owner,
        )

    def test_sees_events_in_owned_calendar(self):
        event = self._make_event(self.alice_cal)
        qs = Event.objects.filter(visible_events_q(self.alice))
        self.assertIn(event, list(qs))

    def test_does_not_see_events_in_other_users_calendar(self):
        event = self._make_event(self.bob_cal)
        qs = Event.objects.filter(visible_events_q(self.alice))
        self.assertNotIn(event, list(qs))

    def test_sees_events_in_subscribed_calendar(self):
        CalendarSubscription.objects.create(user=self.alice, calendar=self.bob_cal)
        event = self._make_event(self.bob_cal)
        qs = Event.objects.filter(visible_events_q(self.alice))
        self.assertIn(event, list(qs))

    def test_sees_events_where_user_is_member(self):
        event = self._make_event(self.bob_cal)
        EventMember.objects.create(event=event, user=self.alice)
        qs = Event.objects.filter(visible_events_q(self.alice))
        self.assertIn(event, list(qs))

    def test_does_not_see_unrelated_events(self):
        carol = User.objects.create_user(username='carol', password='pass')
        carol_cal = Calendar.objects.create(name='Carol Cal', owner=carol)
        event = self._make_event(carol_cal, owner=carol)
        qs = Event.objects.filter(visible_events_q(self.alice))
        self.assertNotIn(event, list(qs))

    def test_no_duplicates_when_owned_and_member(self):
        """Event in owned calendar where user is also a member should appear once."""
        event = self._make_event(self.alice_cal)
        EventMember.objects.create(event=event, user=self.alice)
        qs = Event.objects.filter(visible_events_q(self.alice))
        self.assertEqual(qs.distinct().count(), 1)
