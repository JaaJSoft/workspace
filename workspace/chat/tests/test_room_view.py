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
