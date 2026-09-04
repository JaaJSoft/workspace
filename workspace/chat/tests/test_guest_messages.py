"""Guest reads and posts inside a meeting's conversation, floored to the window.

The floor is created_at >= guest.occurrence_start (the value resolve_guest
already validated for this token) so a guest can never read the conversation's
history from before their occurrence opened, nor reach any other conversation.
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    MeetingGuest,
    Message,
    ThreadParticipant,
)
from workspace.chat.services.meeting_guests import issue_token
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import create_meeting

from .meeting_fixtures import guest_with_token, make_event

User = get_user_model()


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
        return guest_with_token(
            meeting or self.meeting,
            occurrence_start or self.occurrence_start,
            display_name=display_name,
        )

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

    def test_post_rejects_file_uuids(self):
        guest, token = self._admit()
        resp = self._post(token, {"body": "hi", "file_uuids": [str(uuid.uuid4())]})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Message.objects.filter(body="hi").exists())

    def test_post_rejects_duration(self):
        guest, token = self._admit()
        resp = self._post(token, {"body": "hi", "duration": 3.5})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Message.objects.filter(body="hi").exists())

    # --- containment: conversation_id must not reach a guest ---

    def test_read_does_not_expose_conversation_id(self):
        self._make_message(
            self.meeting.conversation, self.occurrence_start, body="during"
        )
        guest, token = self._admit()
        resp = self._get(token)
        self.assertEqual(resp.status_code, 200)
        for message in resp.data["messages"]:
            self.assertNotIn("conversation_id", message)

    def test_post_response_does_not_expose_conversation_id(self):
        guest, token = self._admit()
        resp = self._post(token, {"body": "hello"})
        self.assertEqual(resp.status_code, 201)
        self.assertNotIn("conversation_id", resp.data)

    # --- C1: the reply preview must not defeat the floor ---

    def test_in_window_reply_to_a_pre_window_message_redacts_reply_to(self):
        pre_window = self._make_message(
            self.meeting.conversation,
            self.occurrence_start - timedelta(minutes=1),
            body="before",
        )
        reply = Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.owner,
            body="in window reply",
            reply_to=pre_window,
            thread_root=pre_window,
        )
        Message.objects.filter(pk=reply.pk).update(created_at=self.occurrence_start)
        guest, token = self._admit()

        resp = self._get(token)
        self.assertEqual(resp.status_code, 200)
        bodies = {m["uuid"]: m for m in resp.data["messages"]}
        self.assertIn(str(reply.pk), bodies)
        self.assertIsNone(bodies[str(reply.pk)]["reply_to"])
        self.assertIsNone(bodies[str(reply.pk)]["thread_root"])

    def test_in_window_reply_to_an_in_window_message_keeps_reply_to(self):
        root = self._make_message(
            self.meeting.conversation, self.occurrence_start, body="root"
        )
        reply = Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.owner,
            body="reply",
            reply_to=root,
            thread_root=root,
        )
        Message.objects.filter(pk=reply.pk).update(
            created_at=self.occurrence_start + timedelta(seconds=1)
        )
        guest, token = self._admit()

        resp = self._get(token)
        self.assertEqual(resp.status_code, 200)
        bodies = {m["uuid"]: m for m in resp.data["messages"]}
        self.assertIsNotNone(bodies[str(reply.pk)]["reply_to"])
        self.assertEqual(bodies[str(reply.pk)]["thread_root"], str(root.pk))

    def test_reply_to_uuid_naming_a_pre_floor_message_is_refused(self):
        pre_window = self._make_message(
            self.meeting.conversation,
            self.occurrence_start - timedelta(minutes=1),
            body="before",
        )
        guest, token = self._admit()

        resp = self._post(token, {"body": "hi", "reply_to_uuid": str(pre_window.pk)})
        self.assertEqual(resp.status_code, 400)

        pre_window.refresh_from_db()
        self.assertEqual(pre_window.reply_count, 0)
        self.assertIsNone(pre_window.last_reply_at)
        self.assertFalse(Message.objects.filter(reply_to=pre_window).exists())

    def test_reply_to_uuid_naming_an_in_window_reply_with_a_pre_floor_root_is_refused(
        self,
    ):
        # C1 residual: the reply target itself can be in-window while the
        # thread it belongs to is not - resolve_thread_root hops straight to
        # that pre-floor root, so the floor on reply_to alone is not enough.
        pre_window_root = self._make_message(
            self.meeting.conversation,
            self.occurrence_start - timedelta(minutes=1),
            body="root",
        )
        in_window_reply = Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.owner,
            body="in window reply",
            reply_to=pre_window_root,
            thread_root=pre_window_root,
        )
        Message.objects.filter(pk=in_window_reply.pk).update(
            created_at=self.occurrence_start
        )
        guest, token = self._admit()

        resp = self._post(
            token, {"body": "hi", "reply_to_uuid": str(in_window_reply.pk)}
        )
        self.assertEqual(resp.status_code, 400)

        pre_window_root.refresh_from_db()
        self.assertEqual(pre_window_root.reply_count, 0)
        self.assertIsNone(pre_window_root.last_reply_at)
        self.assertFalse(
            ThreadParticipant.objects.filter(root_message=pre_window_root).exists()
        )
        self.assertFalse(Message.objects.filter(reply_to=in_window_reply).exists())

    # --- M5: pin down two already-correct behaviours ---

    def test_reply_to_uuid_naming_message_in_another_conversation_is_refused(self):
        other_owner = User.objects.create_user(username="other-host", password="x")
        other_conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=other_owner
        )
        ConversationMember.objects.create(conversation=other_conv, user=other_owner)
        other_message = self._make_message(
            other_conv, self.occurrence_start, body="elsewhere", author=other_owner
        )
        guest, token = self._admit()

        resp = self._post(token, {"body": "hi", "reply_to_uuid": str(other_message.pk)})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Message.objects.filter(reply_to=other_message).exists())

    def test_before_cursor_naming_a_pre_floor_message_cannot_page_below_floor(self):
        pre_window = self._make_message(
            self.meeting.conversation,
            self.occurrence_start - timedelta(minutes=1),
            body="before",
        )
        in_window = self._make_message(
            self.meeting.conversation, self.occurrence_start, body="during"
        )
        guest, token = self._admit()

        resp = self.client.get(
            self._url() + f"?before={pre_window.pk}", HTTP_X_MEETING_TOKEN=token
        )
        self.assertEqual(resp.status_code, 200)
        # The cursor names a message the floor already excludes, so it is
        # unknown from this guest's point of view - treated as no cursor at
        # all, never as a doorway to page past the floor.
        bodies = [m["body"] for m in resp.data["messages"]]
        self.assertEqual(bodies, ["during"])
        self.assertEqual(
            {m["uuid"] for m in resp.data["messages"]}, {str(in_window.pk)}
        )
