from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.chat.models import Conversation, Meeting, MeetingGuest
from workspace.chat.services import meetings as meeting_service
from workspace.chat.services.meeting_guests import issue_token, resolve_guest
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import (
    admit_guest,
    create_meeting,
    end_meeting,
    refuse_guest,
    remove_guest,
)


def make_event(owner, start=None):
    cal = Calendar.objects.create(name="Cal", owner=owner)
    return Event.objects.create(
        calendar=cal,
        owner=owner,
        title="Standup",
        start=start or timezone.now(),
    )


class MeetingModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="m", password="x")
        self.event = make_event(self.user)
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )

    def _meeting(self):
        return Meeting.objects.create(
            event=self.event, conversation=self.conv, created_by=self.user
        )

    def test_slug_is_generated_and_unique(self):
        m = self._meeting()
        self.assertTrue(m.slug)
        self.assertGreaterEqual(len(m.slug), 16)

    def test_two_meetings_get_different_slugs(self):
        m1 = self._meeting()
        other_conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        m2 = Meeting.objects.create(
            event=make_event(self.user), conversation=other_conv, created_by=self.user
        )
        self.assertNotEqual(m1.slug, m2.slug)

    def test_join_path_uses_the_slug(self):
        m = self._meeting()
        self.assertEqual(m.join_path, f"/meet/{m.slug}")

    def test_one_meeting_per_event(self):
        self._meeting()
        other_conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        with self.assertRaises(IntegrityError):
            Meeting.objects.create(
                event=self.event, conversation=other_conv, created_by=self.user
            )

    def test_closed_occurrence_start_defaults_to_none(self):
        self.assertIsNone(self._meeting().closed_occurrence_start)


class MeetingGuestModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="g", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        self.meeting = Meeting.objects.create(
            event=make_event(self.user), conversation=self.conv, created_by=self.user
        )

    def test_guest_defaults_to_waiting(self):
        g = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            occurrence_start=timezone.now(),
            token_hash="a" * 64,
        )
        self.assertEqual(g.state, MeetingGuest.State.WAITING)

    def test_token_hash_is_unique(self):
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            occurrence_start=timezone.now(),
            token_hash="b" * 64,
        )
        with self.assertRaises(IntegrityError):
            MeetingGuest.objects.create(
                meeting=self.meeting,
                display_name="Bob",
                occurrence_start=timezone.now(),
                token_hash="b" * 64,
            )


class CreateMeetingTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.owner = User.objects.create_user(username="own", password="x")
        self.invitee = User.objects.create_user(username="inv", password="x")
        self.event = make_event(self.owner)
        EventMember.objects.create(event=self.event, user=self.invitee)

    def tearDown(self):
        cache.clear()

    def test_creates_a_dedicated_conversation_titled_after_the_event(self):
        meeting = create_meeting(self.event, self.owner)
        self.assertEqual(meeting.conversation.title, self.event.title)
        self.assertEqual(meeting.conversation.kind, Conversation.Kind.GROUP)

    def test_seeds_the_owner_and_the_event_members(self):
        meeting = create_meeting(self.event, self.owner)
        member_ids = set(meeting.conversation.members.values_list("user_id", flat=True))
        self.assertEqual(member_ids, {self.owner.id, self.invitee.id})

    def test_is_idempotent_per_event(self):
        first = create_meeting(self.event, self.owner)
        second = create_meeting(self.event, self.owner)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Conversation.objects.filter(meeting__isnull=False).count(), 1)


class MeetingLifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.owner = User.objects.create_user(username="lo", password="x")
        now = timezone.now()
        self.event = make_event(self.owner, start=now - timedelta(minutes=5))
        self.event.end = now + timedelta(minutes=25)
        self.event.save(update_fields=["end"])
        self.meeting = create_meeting(self.event, self.owner)
        # occurrence_start must be current_occurrence()'s own output, not
        # event.start verbatim - the two differ by microseconds (see
        # meeting_occurrences.py's module docstring).
        self.occurrence_start = current_occurrence(self.meeting, now=now)[0]
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            occurrence_start=self.occurrence_start,
            token_hash="e" * 64,
        )

    def tearDown(self):
        cache.clear()

    def test_admit_marks_the_guest_and_records_who(self):
        admit_guest(self.guest, self.owner)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.state, MeetingGuest.State.ADMITTED)
        self.assertEqual(self.guest.admitted_by, self.owner)
        self.assertIsNotNone(self.guest.admitted_at)

    def test_refuse_marks_the_guest(self):
        refuse_guest(self.guest)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.state, MeetingGuest.State.REFUSED)

    def test_remove_marks_the_guest_and_stamps_removed_at(self):
        admit_guest(self.guest, self.owner)
        remove_guest(self.guest)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.state, MeetingGuest.State.REMOVED)
        self.assertIsNotNone(self.guest.removed_at)

    def test_end_records_the_current_occurrence(self):
        self.assertTrue(end_meeting(self.meeting))
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.closed_occurrence_start, self.occurrence_start)

    def test_end_is_a_noop_with_no_reachable_occurrence(self):
        self.event.start = timezone.now() + timedelta(days=30)
        self.event.end = self.event.start + timedelta(hours=1)
        self.event.save(update_fields=["start", "end"])
        self.assertFalse(end_meeting(self.meeting))

    def test_ending_revokes_an_admitted_guest(self):
        token, digest = issue_token()
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Bob",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=self.occurrence_start,
            token_hash=digest,
        )
        self.assertIsNotNone(resolve_guest(token))
        end_meeting(self.meeting)
        self.assertIsNone(resolve_guest(token))

    def test_ending_refuses_waiting_guests_of_this_occurrence(self):
        # A WAITING row for the occurrence being closed must not survive as
        # WAITING forever: the slug is stable for the whole series and
        # nothing else ever purges these rows, so an ignored knock would
        # otherwise count against every future occurrence's lobby cap.
        waiting = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Carol",
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start,
            token_hash="f" * 64,
        )
        end_meeting(self.meeting)
        waiting.refresh_from_db()
        self.assertEqual(waiting.state, MeetingGuest.State.REFUSED)

    def test_ending_leaves_other_occurrences_waiting_guests_alone(self):
        other_occurrence_start = self.occurrence_start - timedelta(days=7)
        other = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Dave",
            state=MeetingGuest.State.WAITING,
            occurrence_start=other_occurrence_start,
            token_hash="g" * 64,
        )
        end_meeting(self.meeting)
        other.refresh_from_db()
        self.assertEqual(other.state, MeetingGuest.State.WAITING)

    def test_end_meeting_is_atomic(self):
        # A crash between the closed_occurrence_start save and the WAITING
        # sweep must not leave the meeting closed with the sweep undone -
        # that is precisely the state nothing else ever purges. Forcing the
        # sweep's own query to raise proves the two writes commit together
        # or not at all.
        with mock.patch(
            "workspace.chat.models.MeetingGuest.objects.filter",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                end_meeting(self.meeting)
        self.meeting.refresh_from_db()
        self.assertIsNone(self.meeting.closed_occurrence_start)


class CreateMeetingRaceTests(TestCase):
    """Two hosts creating a meeting for the same event at once both pass the
    ``existing is None`` check in ``_create_meeting_once``; the loser trips
    ``Meeting.event``'s unique constraint. ``create_meeting`` must recover into
    the winner's row instead of surfacing the IntegrityError as a 500.
    """

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.owner = User.objects.create_user(username="race", password="x")
        self.event = make_event(self.owner)

    def tearDown(self):
        cache.clear()

    def test_compound_race_retries_until_it_finds_the_winner(self):
        # A single retry only closes the two-party race. A compound race (a
        # third caller's insert also lands in the gap) must keep retrying
        # instead of surfacing the second IntegrityError as a 500.
        winner = create_meeting(self.event, self.owner)

        with mock.patch.object(
            meeting_service,
            "_create_meeting_once",
            side_effect=[IntegrityError("x"), IntegrityError("y"), winner],
        ) as once:
            result = create_meeting(self.event, self.owner)

        self.assertEqual(result.pk, winner.pk)
        self.assertEqual(once.call_count, 3)

    def test_unrelated_integrity_error_propagates_without_retry(self):
        # Recovery only applies to the create race, identified by the meeting
        # now existing. Any other IntegrityError is a real failure and must
        # propagate rather than be swallowed.
        with mock.patch.object(
            meeting_service, "_create_meeting_once", side_effect=IntegrityError("boom")
        ) as once:
            with self.assertRaises(IntegrityError):
                create_meeting(self.event, self.owner)

        self.assertEqual(once.call_count, 1)
