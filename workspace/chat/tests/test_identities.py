from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import Conversation, Meeting, MeetingGuest
from workspace.chat.services.identities import (
    display_name_for_identity,
    identity_payload,
)
from workspace.chat.services.participant_keys import guest_key, user_key


class IdentityPayloadTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="ada", password="x", first_name="Ada", last_name="L"
        )
        cal = Calendar.objects.create(name="C", owner=self.user)
        event = Event.objects.create(
            calendar=cal, owner=self.user, title="E", start=timezone.now()
        )
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        meeting = Meeting.objects.create(
            event=event, conversation=conv, created_by=self.user
        )
        self.guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name="Visitor",
            occurrence_start=timezone.now(),
            token_hash="d" * 64,
        )

    def test_member_payload(self):
        data = identity_payload(self.user, None)
        self.assertEqual(data["id"], self.user.id)
        self.assertEqual(data["username"], "ada")
        self.assertEqual(data["display_name"], "Ada L")
        self.assertFalse(data["is_guest"])

    def test_guest_payload_has_no_user_id(self):
        data = identity_payload(None, self.guest)
        self.assertIsNone(data["id"])
        self.assertEqual(data["display_name"], "Visitor")
        self.assertTrue(data["is_guest"])

    def test_member_payload_carries_the_participant_key(self):
        # The key is what a call tile and a message are addressed by, so a
        # reader can tell "this is me" without comparing display names.
        self.assertEqual(
            identity_payload(self.user, None)["participant_key"],
            user_key(self.user.id),
        )

    def test_guest_payload_carries_the_participant_key(self):
        self.assertEqual(
            identity_payload(None, self.guest)["participant_key"],
            guest_key(self.guest.uuid),
        )

    def test_guest_payload_username_is_the_display_name(self):
        # Templates fall back to username in places; a guest has none, so the
        # display name stands in rather than leaking an empty string.
        self.assertEqual(identity_payload(None, self.guest)["username"], "Visitor")

    def test_display_name_prefers_full_name(self):
        self.assertEqual(display_name_for_identity(self.user, None), "Ada L")
        self.assertEqual(display_name_for_identity(None, self.guest), "Visitor")

    def test_display_name_falls_back_to_username(self):
        User = get_user_model()
        bare = User.objects.create_user(username="bare", password="x")
        self.assertEqual(display_name_for_identity(bare, None), "bare")

    def test_neither_identity_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            identity_payload(None, None)
