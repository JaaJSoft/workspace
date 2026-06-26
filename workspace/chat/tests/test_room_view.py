import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.chat.models import Conversation, ConversationMember

User = get_user_model()


class ChatRoomViewTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="member", password="pw")
        self.outsider = User.objects.create_user(username="outsider", password="pw")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.member
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.member
        )

    def _url(self):
        return reverse(
            "chat_ui:room", kwargs={"conversation_uuid": self.conversation.uuid}
        )

    def test_member_gets_room_page(self):
        self.client.force_login(self.member)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "chat/ui/room.html")
        self.assertEqual(
            str(resp.context["conversation_uuid"]), str(self.conversation.uuid)
        )

    def test_non_member_is_forbidden(self):
        self.client.force_login(self.outsider)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_room_conversation_data_is_embedded(self):
        """Regression: the room page must embed a fully-shaped conversation object
        so the reused conversation_pane.html renders the real name instead of
        'Group'. Pins that room-conversation-data carries title, kind, uuid,
        members with user data, and is_bot_conversation."""
        other = User.objects.create_user(username="other_member", password="pw2")
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Team Chat",
            created_by=self.member,
        )
        ConversationMember.objects.create(conversation=conv, user=self.member)
        ConversationMember.objects.create(conversation=conv, user=other)

        self.client.force_login(self.member)
        url = reverse("chat_ui:room", kwargs={"conversation_uuid": conv.uuid})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode()
        self.assertIn(
            '<script id="room-conversation-data" type="application/json">',
            content,
            "room-conversation-data script tag not found in room page",
        )

        m = re.search(
            r'<script id="room-conversation-data" type="application/json">(.*?)</script>',
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "room-conversation-data block not parseable")
        data = json.loads(m.group(1))

        self.assertEqual(data["title"], "Team Chat")
        self.assertEqual(data["kind"], "group")
        self.assertEqual(str(data["uuid"]), str(conv.uuid))
        self.assertIn("is_bot_conversation", data)
        members = data.get("members", [])
        self.assertTrue(len(members) > 0, "members array is empty")
        usernames = {mbr["user"]["username"] for mbr in members}
        self.assertIn("other_member", usernames)
