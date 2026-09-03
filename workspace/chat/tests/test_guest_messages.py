"""Guest reads and posts inside a meeting's conversation, floored to the window.

The floor is created_at >= guest.occurrence_start (the value resolve_guest
already validated for this token) so a guest can never read the conversation's
history from before their occurrence opened, nor reach any other conversation.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    MeetingGuest,
    Message,
)
from workspace.chat.services.meeting_guests import issue_token
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import create_meeting

User = get_user_model()


def make_event(owner, start=None, end=None):
    cal = Calendar.objects.create(name="Cal", owner=owner)
    return Event.objects.create(
        calendar=cal,
        owner=owner,
        title="Standup",
        start=start or timezone.now(),
        end=end,
    )


class GuestMessagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="host", password="x")
        self.now = timezone.now()
        self.event = make_event(
            self.owner,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        self.meeting = create_meeting(self.event, self.owner)
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    def _admit(self, meeting=None, occurrence_start=None, display_name="Ada"):
        meeting = meeting or self.meeting
        token, token_hash = issue_token()
        guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name=display_name,
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=occurrence_start or self.occurrence_start,
            token_hash=token_hash,
        )
        return guest, token

    def _url(self, meeting=None):
        return f"/api/v1/chat/meet/{(meeting or self.meeting).slug}/messages"

    def _get(self, token, meeting=None):
        return self.client.get(self._url(meeting), HTTP_X_MEETING_TOKEN=token)

    def _post(self, token, data, meeting=None):
        return self.client.post(
            self._url(meeting), data, format="json", HTTP_X_MEETING_TOKEN=token
        )

    def _make_message(self, conversation, created_at, body="hi", author=None):
        message = Message.objects.create(
            conversation=conversation,
            author=author or self.owner,
            body=body,
        )
        Message.objects.filter(pk=message.pk).update(created_at=created_at)
        return message

    # --- reads ---

    def test_message_before_occurrence_start_is_absent(self):
        self._make_message(
            self.meeting.conversation,
            self.occurrence_start - timedelta(minutes=1),
            body="before",
        )
        in_window = self._make_message(
            self.meeting.conversation, self.occurrence_start, body="during"
        )
        guest, token = self._admit()

        resp = self._get(token)
        self.assertEqual(resp.status_code, 200)
        bodies = [m["body"] for m in resp.data["messages"]]
        self.assertEqual(bodies, ["during"])
        self.assertEqual(
            {m["uuid"] for m in resp.data["messages"]}, {str(in_window.pk)}
        )

    def test_message_in_another_conversation_is_absent(self):
        other_owner = User.objects.create_user(username="other-host", password="x")
        other_conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=other_owner
        )
        ConversationMember.objects.create(conversation=other_conv, user=other_owner)
        self._make_message(other_conv, self.occurrence_start, body="elsewhere")
        guest, token = self._admit()

        resp = self._get(token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["messages"], [])

    def test_read_requires_admission(self):
        token, token_hash = issue_token()
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start,
            token_hash=token_hash,
        )
        resp = self._get(token)
        self.assertEqual(resp.status_code, 404)

    # --- writes ---

    def test_guest_post_lands_in_meeting_conversation_with_guest_identity(self):
        guest, token = self._admit(display_name="Ada")
        resp = self._post(token, {"body": "hello everyone"})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["body"], "hello everyone")
        self.assertTrue(resp.data["author"]["is_guest"])
        self.assertEqual(resp.data["author"]["display_name"], "Ada")

        message = Message.objects.get(pk=resp.data["uuid"])
        self.assertEqual(message.conversation_id, self.meeting.conversation_id)
        self.assertIsNone(message.author_id)
        self.assertEqual(message.guest_id, guest.uuid)

    def test_guest_post_bumps_member_unread_count(self):
        member = ConversationMember.objects.get(
            conversation=self.meeting.conversation, user=self.owner
        )
        self.assertEqual(member.unread_count, 0)
        guest, token = self._admit()
        self._post(token, {"body": "hello"})
        member.refresh_from_db()
        self.assertEqual(member.unread_count, 1)

    def test_body_naming_another_conversation_is_ignored(self):
        other_owner = User.objects.create_user(username="other-host", password="x")
        other_conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=other_owner
        )
        ConversationMember.objects.create(conversation=other_conv, user=other_owner)
        guest, token = self._admit()

        resp = self._post(token, {"body": "hi", "conversation_id": str(other_conv.pk)})
        self.assertEqual(resp.status_code, 201)

        message = Message.objects.get(pk=resp.data["uuid"])
        self.assertEqual(message.conversation_id, self.meeting.conversation_id)
        self.assertFalse(Message.objects.filter(conversation=other_conv).exists())

    def test_post_requires_admission(self):
        token, token_hash = issue_token()
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start,
            token_hash=token_hash,
        )
        resp = self._post(token, {"body": "hello"})
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Message.objects.filter(body="hello").exists())

    def test_post_requires_a_body(self):
        guest, token = self._admit()
        resp = self._post(token, {"body": "  "})
        self.assertEqual(resp.status_code, 400)
