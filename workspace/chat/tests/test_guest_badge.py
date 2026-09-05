"""A guest author renders visibly differently from a member: the guest
attribute on <chat-message-group> (message_shell.js turns it into a badge),
and the hover toolbar showing Delete - never Edit - for a guest message,
since the lot 3A fix wave lets any active member delete one through the API.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.chat.models import MeetingGuest, Message
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import create_meeting

from .meeting_fixtures import guest_with_token, make_event

User = get_user_model()


class GuestBadgeTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user("alice", "alice@example.com", "pw")
        event = make_event(
            self.host, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        self.meeting = create_meeting(event, self.host)
        start = current_occurrence(self.meeting)[0]
        self.guest, _ = guest_with_token(
            self.meeting,
            start,
            display_name="alice",
            state=MeetingGuest.State.ADMITTED,
        )
        self.client.force_login(self.host)
        self.url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.meeting.conversation_id},
        )

    def test_a_guest_message_group_carries_the_guest_attribute_and_a_member_does_not(
        self,
    ):
        Message.objects.create(
            conversation=self.meeting.conversation,
            guest=self.guest,
            body="hi from outside",
        )
        Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.host,
            body="hi from inside",
        )
        resp = self.client.get(self.url, HTTP_X_ALPINE_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        groups = [
            g
            for g in html.split("<chat-message-group")
            if 'author-username="alice"' in g
        ]
        self.assertEqual(len(groups), 2)
        guest_groups = [g for g in groups if " guest" in g.split(">", 1)[0]]
        self.assertEqual(len(guest_groups), 1)
        self.assertIn("hi from outside", guest_groups[0])

    def test_a_guest_group_shows_delete_never_edit_for_the_viewing_host(self):
        Message.objects.create(
            conversation=self.meeting.conversation,
            guest=self.guest,
            body="hi from outside",
        )
        resp = self.client.get(self.url, HTTP_X_ALPINE_REQUEST="true")
        html = resp.content.decode()
        guest_group = next(
            g
            for g in html.split("<chat-message-group")
            if " guest" in g.split(">", 1)[0]
        )
        self.assertIn("deleteMessage(", guest_group)
        self.assertNotIn("startEdit(", guest_group)

    def test_another_members_own_group_shows_neither_action_for_a_different_viewer(
        self,
    ):
        bob = User.objects.create_user("bob", "bob@example.com", "pw")
        Message.objects.create(
            conversation=self.meeting.conversation,
            author=bob,
            body="hi, I'm bob",
        )
        resp = self.client.get(self.url, HTTP_X_ALPINE_REQUEST="true")
        html = resp.content.decode()
        bob_group = next(
            g for g in html.split("<chat-message-group") if 'author-username="bob"' in g
        )
        self.assertNotIn("deleteMessage(", bob_group)
        self.assertNotIn("startEdit(", bob_group)
