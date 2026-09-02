from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import Conversation, Meeting, MeetingGuest
from workspace.chat.services.meeting_guests import (
    guest_for_token,
    hash_token,
    issue_token,
    resolve_guest,
)
from workspace.chat.services.meeting_occurrences import current_occurrence


class TokenTests(TestCase):
    def test_issue_returns_a_token_and_its_hash(self):
        token, digest = issue_token()
        self.assertGreaterEqual(len(token), 32)
        self.assertEqual(digest, hash_token(token))
        self.assertEqual(len(digest), 64)

    def test_two_tokens_differ(self):
        self.assertNotEqual(issue_token()[0], issue_token()[0])


class ResolveGuestTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rg", password="x")
        cal = Calendar.objects.create(name="C", owner=self.user)
        self.now = timezone.now()
        self.event = Event.objects.create(
            calendar=cal,
            owner=self.user,
            title="E",
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        self.meeting = Meeting.objects.create(
            event=self.event, conversation=conv, created_by=self.user
        )
        # occurrence_start must be current_occurrence()'s own output, not
        # event.start verbatim - the two differ by microseconds (see
        # meeting_occurrences.py's module docstring).
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]
        self.token, digest = issue_token()
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=self.occurrence_start,
            token_hash=digest,
        )

    def test_admitted_guest_inside_the_window_resolves(self):
        self.assertEqual(resolve_guest(self.token, now=self.now), self.guest)

    def test_unknown_token_is_rejected(self):
        self.assertIsNone(resolve_guest("nope", now=self.now))

    def test_empty_or_none_token_is_rejected(self):
        self.assertIsNone(resolve_guest("", now=self.now))
        self.assertIsNone(resolve_guest(None, now=self.now))

    def test_lone_surrogate_token_is_rejected_without_raising(self):
        # A JSON body can carry an unpaired surrogate (json.loads accepts it);
        # sha256 cannot encode it. The gate must reject, not 500.
        self.assertIsNone(resolve_guest("\ud800", now=self.now))

    def test_waiting_guest_is_rejected(self):
        self.guest.state = MeetingGuest.State.WAITING
        self.guest.save(update_fields=["state"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))

    def test_refused_guest_is_rejected(self):
        self.guest.state = MeetingGuest.State.REFUSED
        self.guest.save(update_fields=["state"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))

    def test_removed_guest_is_rejected(self):
        self.guest.state = MeetingGuest.State.REMOVED
        self.guest.save(update_fields=["state"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))

    def test_rejected_before_the_window_opens(self):
        self.assertIsNone(resolve_guest(self.token, now=self.now - timedelta(hours=3)))

    def test_rejected_after_the_window_closes(self):
        self.assertIsNone(resolve_guest(self.token, now=self.now + timedelta(hours=3)))

    def test_rejected_when_the_host_ended_this_occurrence(self):
        self.meeting.closed_occurrence_start = self.occurrence_start
        self.meeting.save(update_fields=["closed_occurrence_start"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))

    def test_still_valid_when_a_different_occurrence_was_ended(self):
        self.meeting.closed_occurrence_start = self.occurrence_start - timedelta(days=7)
        self.meeting.save(update_fields=["closed_occurrence_start"])
        self.assertIsNotNone(resolve_guest(self.token, now=self.now))

    def test_rejected_when_the_guest_belongs_to_another_occurrence(self):
        self.guest.occurrence_start = self.occurrence_start - timedelta(days=7)
        self.guest.save(update_fields=["occurrence_start"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))


class GuestForTokenTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="gft", password="x")
        cal = Calendar.objects.create(name="C", owner=self.user)
        self.now = timezone.now()
        self.event = Event.objects.create(
            calendar=cal,
            owner=self.user,
            title="E",
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        self.meeting = Meeting.objects.create(
            event=self.event, conversation=conv, created_by=self.user
        )
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]
        self.token, digest = issue_token()
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=self.occurrence_start,
            token_hash=digest,
        )

    def test_finds_a_waiting_guest_that_resolve_guest_rejects(self):
        self.guest.state = MeetingGuest.State.WAITING
        self.guest.save(update_fields=["state"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))
        self.assertEqual(guest_for_token(self.token), self.guest)

    def test_finds_a_refused_guest(self):
        self.guest.state = MeetingGuest.State.REFUSED
        self.guest.save(update_fields=["state"])
        self.assertEqual(guest_for_token(self.token), self.guest)

    def test_rejects_an_unknown_token(self):
        self.assertIsNone(guest_for_token("nope"))

    def test_rejects_garbage_without_raising(self):
        for bad in (None, "", 12, [], "\ud800"):
            with self.subTest(bad=bad):
                self.assertIsNone(guest_for_token(bad))


class ResolveGuestRecurringTests(TestCase):
    """The headline claims of the design, on an actual recurring series:
    last week's token does not open this week's occurrence, and closing
    today's occurrence leaves next week's link open.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rgr", password="x")
        cal = Calendar.objects.create(name="C", owner=self.user)
        self.now = timezone.now()
        self.event = Event.objects.create(
            calendar=cal,
            owner=self.user,
            title="Standup",
            start=self.now - timedelta(weeks=3, minutes=5),
            end=self.now - timedelta(weeks=3) + timedelta(minutes=25),
            recurrence_frequency=Event.RecurrenceFrequency.WEEKLY,
            recurrence_interval=1,
        )
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        self.meeting = Meeting.objects.create(
            event=self.event, conversation=conv, created_by=self.user
        )
        occurrence = current_occurrence(self.meeting, now=self.now)
        assert occurrence is not None
        self.occurrence_start = occurrence[0]
        self.token, digest = issue_token()
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=self.occurrence_start,
            token_hash=digest,
        )

    def test_admitted_guest_resolves_for_this_weeks_occurrence(self):
        self.assertEqual(resolve_guest(self.token, now=self.now), self.guest)

    def test_last_weeks_occurrence_start_is_rejected(self):
        self.guest.occurrence_start = self.occurrence_start - timedelta(weeks=1)
        self.guest.save(update_fields=["occurrence_start"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))

    def test_closing_this_weeks_occurrence_revokes_the_token(self):
        self.meeting.closed_occurrence_start = self.occurrence_start
        self.meeting.save(update_fields=["closed_occurrence_start"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))

    def test_closing_last_weeks_occurrence_leaves_this_weeks_link_open(self):
        self.meeting.closed_occurrence_start = self.occurrence_start - timedelta(
            weeks=1
        )
        self.meeting.save(update_fields=["closed_occurrence_start"])
        self.assertIsNotNone(resolve_guest(self.token, now=self.now))
