from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import Conversation, Meeting, MeetingGuest
from workspace.chat.services.meeting_guests import (
    hash_token,
    issue_token,
    resolve_guest,
)


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
        self.token, digest = issue_token()
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=self.event.start,
            token_hash=digest,
        )

    def test_admitted_guest_inside_the_window_resolves(self):
        self.assertEqual(resolve_guest(self.token, now=self.now), self.guest)

    def test_unknown_token_is_rejected(self):
        self.assertIsNone(resolve_guest("nope", now=self.now))

    def test_empty_or_none_token_is_rejected(self):
        self.assertIsNone(resolve_guest("", now=self.now))
        self.assertIsNone(resolve_guest(None, now=self.now))

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
        self.meeting.closed_occurrence_start = self.event.start
        self.meeting.save(update_fields=["closed_occurrence_start"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))

    def test_still_valid_when_a_different_occurrence_was_ended(self):
        self.meeting.closed_occurrence_start = self.event.start - timedelta(days=7)
        self.meeting.save(update_fields=["closed_occurrence_start"])
        self.assertIsNotNone(resolve_guest(self.token, now=self.now))

    def test_rejected_when_the_guest_belongs_to_another_occurrence(self):
        self.guest.occurrence_start = self.event.start - timedelta(days=7)
        self.guest.save(update_fields=["occurrence_start"])
        self.assertIsNone(resolve_guest(self.token, now=self.now))
