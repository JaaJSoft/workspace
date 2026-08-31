from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import Conversation, Meeting, MeetingGuest


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
