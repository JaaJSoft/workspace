import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from pydantic import ValidationError

from workspace.calendar.ai_tools import (
    CalendarToolProvider,
    CancelEventParams,
    CheckAvailabilityParams,
    CreateEventParams,
    CreatePollParams,
    GetPollResultsParams,
    ListUpcomingEventsParams,
    RespondToInvitationParams,
    UpdateEventParams,
)
from workspace.calendar.models import Calendar, Event, EventMember, Poll, PollVote
from workspace.calendar.models_external import ExternalCalendar
from workspace.calendar.services.recurrence_rule import apply_rule
from workspace.mail.models import MailAccount, MailFolder, MailMessage

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

    def test_list_upcoming_events_exposes_ids_the_edit_tools_accept(self):
        cal = Calendar.objects.create(name="Work", owner=self.user)
        now = timezone.now()
        one_off = Event.objects.create(
            calendar=cal, owner=self.user, title="Solo", start=now + timedelta(days=1)
        )
        master = Event(
            calendar=cal,
            owner=self.user,
            title="Weekly",
            start=now + timedelta(days=2),
            end=now + timedelta(days=2, hours=1),
        )
        apply_rule(master, "RRULE:FREQ=WEEKLY")
        master.save()

        args = ListUpcomingEventsParams(days_ahead=7, limit=20)
        entries = json.loads(
            self.provider.list_upcoming_events(
                args, user=self.user, bot=None, conversation_id=None, context={}
            )
        )
        by_title = {e["title"]: e for e in entries}

        self.assertEqual(by_title["Solo"]["event_id"], str(one_off.uuid))
        self.assertNotIn("original_start", by_title["Solo"])
        # A recurring occurrence points at its master, not at the synthetic
        # "<master>:<start>" id, and carries the original_start the scoped
        # edit tools need.
        self.assertEqual(by_title["Weekly"]["event_id"], str(master.uuid))
        self.assertTrue(by_title["Weekly"]["recurring"])
        self.assertIn("original_start", by_title["Weekly"])

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

    def test_list_calendars_empty(self):
        result = self.provider.list_calendars(
            {}, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertEqual(result, "You have no calendars yet.")

    def test_list_upcoming_events_clamps_limit(self):
        cal = Calendar.objects.create(name="Work", owner=self.user)
        Event.objects.create(
            calendar=cal,
            owner=self.user,
            title="First",
            start=timezone.now() + timedelta(days=1),
        )
        Event.objects.create(
            calendar=cal,
            owner=self.user,
            title="Second",
            start=timezone.now() + timedelta(days=2),
        )
        # limit=0 is clamped up to 1, so exactly the soonest event is returned.
        args = ListUpcomingEventsParams(days_ahead=7, limit=0)
        result = self.provider.list_upcoming_events(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        data = json.loads(result)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "First")

    def test_list_upcoming_events_clamps_days_ahead(self):
        cal = Calendar.objects.create(name="Work", owner=self.user)
        Event.objects.create(
            calendar=cal,
            owner=self.user,
            title="HalfDay",
            start=timezone.now() + timedelta(hours=12),
        )
        # days_ahead=0 is clamped up to 1, so an event 12h out stays in the window.
        args = ListUpcomingEventsParams(days_ahead=0, limit=20)
        result = self.provider.list_upcoming_events(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("HalfDay", result)

    def _future_iso(self, **delta):
        return (timezone.now() + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M")

    def test_check_availability_reports_a_free_range(self):
        Calendar.objects.create(name="Work", owner=self.user)
        start = timezone.now() + timedelta(days=1)
        args = CheckAvailabilityParams(
            start=start.isoformat(), end=(start + timedelta(hours=1)).isoformat()
        )
        payload = json.loads(
            self.provider.check_availability(
                args, user=self.user, bot=None, conversation_id=None, context={}
            )
        )
        self.assertTrue(payload["available"])
        self.assertEqual(payload["events"], [])

    def test_check_availability_reports_the_conflicting_event(self):
        cal = Calendar.objects.create(name="Work", owner=self.user)
        start = timezone.now() + timedelta(days=1)
        Event.objects.create(
            calendar=cal,
            owner=self.user,
            title="Busy",
            start=start,
            end=start + timedelta(hours=2),
        )
        args = CheckAvailabilityParams(
            start=(start + timedelta(minutes=30)).isoformat(),
            end=(start + timedelta(hours=1)).isoformat(),
        )
        payload = json.loads(
            self.provider.check_availability(
                args, user=self.user, bot=None, conversation_id=None, context={}
            )
        )
        self.assertFalse(payload["available"])
        self.assertEqual([e["title"] for e in payload["events"]], ["Busy"])

    def test_check_availability_rejects_an_unparseable_range(self):
        args = CheckAvailabilityParams(start="next tuesday", end="2026-03-21T10:00")
        result = self.provider.check_availability(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("could not parse start datetime", result)

    def test_check_availability_rejects_an_inverted_range(self):
        args = CheckAvailabilityParams(start="2026-03-21T10:00", end="2026-03-21T09:00")
        result = self.provider.check_availability(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("end must be after start", result)

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
        self.assertEqual(ev.source, Event.Source.MANUAL)

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

    def test_create_event_all_day_today_allowed(self):
        Calendar.objects.create(name="Perso", owner=self.user)
        today = timezone.now().strftime("%Y-%m-%d")
        args = CreateEventParams(title="Holiday", start=today, all_day=True)
        result = self.provider.create_event(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("Created event", result)
        ev = Event.objects.get(title="Holiday")
        self.assertTrue(ev.all_day)

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

    def test_create_event_params_reject_overlong_title(self):
        # Title over the Event model's max_length (255) is rejected at the
        # tool-call boundary instead of reaching the database.
        with self.assertRaises(ValidationError):
            CreateEventParams(title="x" * 256, start=self._future_iso(days=1))


class CalendarWriteToolsTests(TestCase):
    """update_event / cancel_event / respond_to_invitation."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")
        self.calendar = Calendar.objects.create(name="Work", owner=self.user)
        self.provider = CalendarToolProvider()

    def tearDown(self):
        cache.clear()

    def _event(self, **kwargs):
        defaults = {
            "calendar": self.calendar,
            "owner": self.user,
            "title": "Standup",
            "start": timezone.now() + timedelta(days=1),
            "end": timezone.now() + timedelta(days=1, hours=1),
        }
        return Event.objects.create(**{**defaults, **kwargs})

    def _weekly(self):
        start = (timezone.now() + timedelta(days=1)).replace(
            minute=0, second=0, microsecond=0
        )
        event = Event(
            calendar=self.calendar,
            owner=self.user,
            title="Weekly sync",
            start=start,
            end=start + timedelta(hours=1),
        )
        apply_rule(event, "RRULE:FREQ=WEEKLY")
        event.save()
        return event

    def _update(self, context=None, **kwargs):
        args = UpdateEventParams(**kwargs)
        return self.provider.update_event(
            args,
            user=self.user,
            bot=None,
            conversation_id=None,
            context=context if context is not None else {},
        )

    def _cancel(self, context=None, **kwargs):
        args = CancelEventParams(**kwargs)
        return self.provider.cancel_event(
            args,
            user=self.user,
            bot=None,
            conversation_id=None,
            context=context if context is not None else {},
        )

    # -- update_event ----------------------------------------------------

    def test_update_renames_a_one_off_event(self):
        event = self._event()
        result = self._update(event_id=event.uuid, scope="all", title="Retro")
        event.refresh_from_db()
        self.assertEqual(event.title, "Retro")
        self.assertIn("Retro", result)

    def test_update_moves_start_and_end(self):
        event = self._event()
        new_start = (timezone.now() + timedelta(days=3)).replace(microsecond=0)
        self._update(
            event_id=event.uuid,
            scope="all",
            start=new_start.isoformat(),
            end=(new_start + timedelta(hours=2)).isoformat(),
        )
        event.refresh_from_db()
        self.assertEqual(event.start, new_start)
        self.assertEqual(event.end, new_start + timedelta(hours=2))

    def test_update_clears_location_with_the_none_sentinel(self):
        event = self._event(location="Room 3")
        self._update(event_id=event.uuid, scope="all", location="none")
        event.refresh_from_db()
        self.assertEqual(event.location, "")

    def test_update_replaces_the_guest_list(self):
        event = self._event()
        EventMember.objects.create(event=event, user=self.other)
        carol = User.objects.create_user(username="carol", password="pw")

        self._update(
            event_id=event.uuid, scope="all", attendees=["Carol"], confirm=True
        )

        self.assertEqual(
            list(event.members.values_list("user_id", flat=True)), [carol.id]
        )

    def test_update_removes_every_guest_with_the_none_sentinel(self):
        event = self._event()
        EventMember.objects.create(event=event, user=self.other)
        self._update(event_id=event.uuid, scope="all", attendees=["none"], confirm=True)
        self.assertEqual(event.members.count(), 0)

    def test_update_rejects_an_unknown_attendee_without_writing(self):
        event = self._event()
        result = self._update(
            event_id=event.uuid, scope="all", title="Retro", attendees=["nobody"]
        )
        event.refresh_from_db()
        self.assertIn("no active user named nobody", result)
        self.assertEqual(event.title, "Standup")

    def test_update_rejects_an_end_before_the_untouched_start(self):
        event = self._event()
        result = self._update(
            event_id=event.uuid,
            scope="all",
            end=(event.start - timedelta(hours=1)).isoformat(),
        )
        self.assertIn("end must be after start", result)

    def test_update_rejects_an_empty_change(self):
        event = self._event()
        result = self._update(event_id=event.uuid, scope="all")
        self.assertIn("nothing to change", result)

    def test_update_refuses_someone_elses_event(self):
        foreign_cal = Calendar.objects.create(name="Bob", owner=self.other)
        event = Event.objects.create(
            calendar=foreign_cal,
            owner=self.other,
            title="Private",
            start=timezone.now() + timedelta(days=1),
        )
        EventMember.objects.create(event=event, user=self.user)

        result = self._update(event_id=event.uuid, scope="all", title="Hijacked")
        event.refresh_from_db()
        self.assertIn("Only the owner", result)
        self.assertEqual(event.title, "Private")

    def test_update_refuses_an_external_calendar(self):
        ext = Calendar.objects.create(name="Holidays", owner=self.user)
        ExternalCalendar.objects.create(
            calendar=ext, url="https://example.com/feed.ics"
        )
        event = self._event(calendar=ext)

        result = self._update(event_id=event.uuid, scope="all", title="Nope")
        event.refresh_from_db()
        self.assertIn("external calendar", result)
        self.assertEqual(event.title, "Standup")

    def test_update_refuses_an_invisible_event(self):
        foreign_cal = Calendar.objects.create(name="Bob", owner=self.other)
        event = Event.objects.create(
            calendar=foreign_cal,
            owner=self.other,
            title="Secret",
            start=timezone.now() + timedelta(days=1),
        )
        result = self._update(event_id=event.uuid, scope="all", title="Nope")
        self.assertIn("no event with that id", result)

    def test_update_of_a_series_asks_for_confirmation_first(self):
        master = self._weekly()
        context = {}
        result = self._update(
            context=context, event_id=master.uuid, scope="all", title="Renamed"
        )
        master.refresh_from_db()

        self.assertTrue(context["stop_after_round"])
        self.assertIn("whole recurring series", context["question"]["question"])
        self.assertIn("confirm=true", result)
        self.assertEqual(master.title, "Weekly sync")

    def test_confirmed_update_of_a_single_occurrence_creates_an_exception(self):
        master = self._weekly()
        occurrence = master.start + timedelta(weeks=1)
        self._update(
            event_id=master.uuid,
            scope="this",
            original_start=occurrence.isoformat(),
            title="Moved",
            confirm=True,
        )
        exceptions = Event.objects.filter(recurrence_parent=master)
        self.assertEqual(exceptions.count(), 1)
        self.assertEqual(exceptions.first().title, "Moved")
        master.refresh_from_db()
        self.assertEqual(master.title, "Weekly sync")

    def test_moving_one_occurrence_keeps_the_series_duration(self):
        master = self._weekly()
        occurrence = master.start + timedelta(weeks=1)
        moved_to = occurrence + timedelta(hours=5)

        self._update(
            event_id=master.uuid,
            scope="this",
            original_start=occurrence.isoformat(),
            start=moved_to.isoformat(),
            confirm=True,
        )

        exception = Event.objects.get(recurrence_parent=master)
        self.assertEqual(exception.start, moved_to)
        self.assertEqual(exception.end, moved_to + timedelta(hours=1))

    def test_scoped_update_rejects_an_end_before_the_occurrence_start(self):
        master = self._weekly()
        third = master.start + timedelta(weeks=2)
        # An end taken from the FIRST occurrence: later than the master start,
        # so a guard anchored on the master would wave it through and write an
        # occurrence finishing two weeks before it begins.
        end_in_week_one = master.start + timedelta(hours=1)

        result = self._update(
            event_id=master.uuid,
            scope="this",
            original_start=third.isoformat(),
            end=end_in_week_one.isoformat(),
            confirm=True,
        )

        self.assertIn("end must be after start", result)
        self.assertFalse(Event.objects.filter(recurrence_parent=master).exists())

    def test_changing_the_guest_list_asks_for_confirmation(self):
        event = self._event()
        context = {}
        result = self._update(
            context=context, event_id=event.uuid, scope="all", attendees=["bob"]
        )

        self.assertTrue(context["stop_after_round"])
        self.assertIn("confirm=true", result)
        self.assertEqual(event.members.count(), 0)

    def test_scoped_update_requires_an_original_start(self):
        master = self._weekly()
        result = self._update(
            event_id=master.uuid, scope="future", title="X", confirm=True
        )
        self.assertIn("original_start is required", result)
        self.assertFalse(Event.objects.filter(recurrence_parent=master).exists())

    def test_scoped_update_refuses_an_instant_off_the_series_grid(self):
        master = self._weekly()
        off_grid = master.start + timedelta(weeks=1, minutes=17)
        result = self._update(
            event_id=master.uuid,
            scope="this",
            original_start=off_grid.isoformat(),
            title="X",
            confirm=True,
        )
        self.assertIn("not an occurrence of this series", result)
        self.assertFalse(Event.objects.filter(recurrence_parent=master).exists())

    def test_scope_is_a_required_argument(self):
        with self.assertRaises(ValidationError):
            UpdateEventParams(event_id=self._event().uuid, title="X")

    # -- cancel_event ----------------------------------------------------

    def test_cancel_asks_for_confirmation_before_deleting(self):
        event = self._event()
        context = {}
        result = self._cancel(context=context, event_id=event.uuid, scope="all")

        self.assertTrue(context["stop_after_round"])
        self.assertIn("confirm=true", result)
        self.assertTrue(Event.objects.filter(uuid=event.uuid).exists())

    def test_confirmed_cancel_deletes_the_event(self):
        event = self._event()
        result = self._cancel(event_id=event.uuid, scope="all", confirm=True)
        self.assertFalse(Event.objects.filter(uuid=event.uuid).exists())
        self.assertIn("Cancelled", result)

    def test_confirmed_cancel_of_one_occurrence_keeps_the_series(self):
        master = self._weekly()
        occurrence = master.start + timedelta(weeks=2)
        self._cancel(
            event_id=master.uuid,
            scope="this",
            original_start=occurrence.isoformat(),
            confirm=True,
        )
        self.assertTrue(Event.objects.filter(uuid=master.uuid).exists())
        self.assertEqual(
            Event.objects.filter(recurrence_parent=master, is_cancelled=True).count(),
            1,
        )

    def test_cancel_refuses_an_external_calendar(self):
        ext = Calendar.objects.create(name="Holidays", owner=self.user)
        ExternalCalendar.objects.create(
            calendar=ext, url="https://example.com/feed.ics"
        )
        event = self._event(calendar=ext)
        result = self._cancel(event_id=event.uuid, scope="all", confirm=True)
        self.assertIn("external calendar", result)
        self.assertTrue(Event.objects.filter(uuid=event.uuid).exists())

    # -- respond_to_invitation -------------------------------------------

    def _respond(self, context=None, **kwargs):
        args = RespondToInvitationParams(**kwargs)
        return self.provider.respond_to_invitation(
            args,
            user=self.user,
            bot=None,
            conversation_id=None,
            context=context if context is not None else {},
        )

    def _invitation(self):
        foreign_cal = Calendar.objects.create(name="Bob", owner=self.other)
        event = Event.objects.create(
            calendar=foreign_cal,
            owner=self.other,
            title="Kickoff",
            start=timezone.now() + timedelta(days=2),
        )
        membership = EventMember.objects.create(event=event, user=self.user)
        return event, membership

    def test_answering_an_invitation_asks_for_confirmation_first(self):
        event, membership = self._invitation()
        context = {}
        result = self._respond(
            context=context, event_id=event.uuid, response="accepted"
        )

        membership.refresh_from_db()
        self.assertTrue(context["stop_after_round"])
        self.assertIn("Accept the invitation", context["question"]["question"])
        self.assertIn("confirm=true", result)
        self.assertEqual(membership.status, EventMember.Status.PENDING)

    def test_confirmed_answer_writes_the_membership_status(self):
        event, membership = self._invitation()
        result = self._respond(event_id=event.uuid, response="declined", confirm=True)
        membership.refresh_from_db()
        self.assertEqual(membership.status, EventMember.Status.DECLINED)
        self.assertIn("declined", result)

    @patch("workspace.calendar.tasks.send_ics_reply.delay")
    def test_confirmed_answer_replies_to_an_external_organiser(self, mock_delay):
        event, _membership = self._invitation()
        account = MailAccount.objects.create(
            owner=self.other,
            email="bob@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
        )
        message = MailMessage.objects.create(
            account=account,
            folder=MailFolder.objects.create(
                account=account, name="INBOX", folder_type="inbox"
            ),
            imap_uid=1,
            message_id="<kickoff@example.com>",
        )
        event.external_organizer = "organiser@example.com"
        event.source_message = message
        event.save(update_fields=["external_organizer", "source_message"])

        self._respond(event_id=event.uuid, response="accepted", confirm=True)

        mock_delay.assert_called_once_with(str(event.uuid), self.user.id, "accepted")

    def test_answering_an_event_you_were_not_invited_to_errors(self):
        event = self._event()
        result = self._respond(event_id=event.uuid, response="accepted", confirm=True)
        self.assertIn("not on the guest list", result)


class CalendarPollToolsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")
        self.provider = CalendarToolProvider()

    def tearDown(self):
        cache.clear()

    def _slots(self, count=3):
        base = (timezone.now() + timedelta(days=2)).replace(
            minute=0, second=0, microsecond=0
        )
        return [(base + timedelta(days=i)).isoformat() for i in range(count)]

    def _create_poll(self, **kwargs):
        kwargs.setdefault("title", "Team lunch")
        kwargs.setdefault("slots", self._slots())
        args = CreatePollParams(**kwargs)
        return self.provider.create_poll(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )

    def test_create_poll_stores_slots_and_invitees(self):
        result = self._create_poll(invitees=["bob"], duration_minutes=90)

        poll = Poll.objects.get(created_by=self.user)
        self.assertEqual(poll.title, "Team lunch")
        self.assertEqual(poll.slots.count(), 3)
        first = poll.slots.first()
        self.assertEqual(first.end - first.start, timedelta(minutes=90))
        self.assertEqual(
            list(poll.invitees.values_list("user_id", flat=True)), [self.other.id]
        )
        self.assertIn(str(poll.uuid), result)

    def test_create_poll_needs_at_least_two_distinct_slots(self):
        repeated = self._slots(1) * 2
        result = self._create_poll(slots=repeated)
        self.assertIn("at least 2 distinct", result)
        self.assertFalse(Poll.objects.exists())

    def test_create_poll_rejects_slots_in_the_past(self):
        past = [(timezone.now() - timedelta(days=i)).isoformat() for i in range(1, 3)]
        result = self._create_poll(slots=past)
        self.assertIn("must be in the future", result)
        self.assertFalse(Poll.objects.exists())

    def test_create_poll_rejects_an_unknown_invitee(self):
        result = self._create_poll(invitees=["ghost"])
        self.assertIn("no active user named ghost", result)
        self.assertFalse(Poll.objects.exists())

    def test_poll_results_report_votes_and_the_leading_slot(self):
        self._create_poll(invitees=["bob"])
        poll = Poll.objects.get(created_by=self.user)
        winner, runner_up = list(poll.slots.all())[:2]
        PollVote.objects.create(slot=winner, user=self.other, choice="yes")
        PollVote.objects.create(slot=runner_up, user=self.other, choice="no")

        args = GetPollResultsParams(poll_id=poll.uuid)
        payload = json.loads(
            self.provider.get_poll_results(
                args, user=self.user, bot=None, conversation_id=None, context={}
            )
        )

        self.assertEqual(payload["title"], "Team lunch")
        self.assertEqual(payload["invitees"], ["bob"])
        self.assertEqual(payload["slots"][0]["yes"], 1)
        self.assertEqual(payload["slots"][0]["voters"]["yes"], ["bob"])
        self.assertEqual(payload["slots"][1]["no"], 1)
        self.assertEqual(
            payload["leading_slot"],
            winner.start.astimezone(timezone.get_current_timezone()).strftime(
                "%Y-%m-%d %H:%M"
            ),
        )

    def test_poll_results_say_so_when_nobody_voted(self):
        self._create_poll()
        poll = Poll.objects.get(created_by=self.user)
        args = GetPollResultsParams(poll_id=poll.uuid)
        payload = json.loads(
            self.provider.get_poll_results(
                args, user=self.user, bot=None, conversation_id=None, context={}
            )
        )
        self.assertEqual(payload["note"], "Nobody has voted yet.")
        self.assertNotIn("leading_slot", payload)

    def test_poll_results_refuse_a_poll_the_user_has_no_part_in(self):
        poll = Poll.objects.create(title="Private", created_by=self.other)
        args = GetPollResultsParams(poll_id=poll.uuid)
        result = self.provider.get_poll_results(
            args, user=self.user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("no access", result)
