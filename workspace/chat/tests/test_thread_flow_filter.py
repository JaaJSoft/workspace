from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.users.services.settings import set_setting

User = get_user_model()


class MainFlowFilterTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="secret")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice
        )
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="the root message"
        )
        self.reply = Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="the threaded reply",
            reply_to=self.root,
            thread_root=self.root,
        )
        self.client.force_login(self.alice)
        self.url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.conversation.uuid},
        )

    def tearDown(self):
        cache.clear()

    def test_the_main_flow_hides_thread_replies_by_default(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("the root message", html)
        self.assertNotIn("the threaded reply", html)

    def test_the_preference_brings_them_back_inline(self):
        set_setting(
            self.alice, "chat", "preferences", {"showThreadRepliesInline": True}
        )
        html = self.client.get(self.url).content.decode()
        self.assertIn("the root message", html)
        self.assertIn("the threaded reply", html)

    def _api_messages(self):
        api = APIClient()
        api.force_authenticate(user=self.alice)
        url = reverse(
            "chat-messages", kwargs={"conversation_id": self.conversation.uuid}
        )
        return api.get(url).data["messages"]

    def test_the_api_message_list_always_includes_thread_replies(self):
        # The inline preference shapes the server-rendered partial only: a UI
        # preference must not shrink an API response. Clients that want the
        # main flow filter on thread_root themselves.
        bodies = [m["body"] for m in self._api_messages()]
        self.assertEqual(bodies, ["the root message", "the threaded reply"])

    def test_the_api_message_list_ignores_the_preference(self):
        set_setting(
            self.alice, "chat", "preferences", {"showThreadRepliesInline": False}
        )
        by_body = {m["body"]: m for m in self._api_messages()}
        self.assertIn("the threaded reply", by_body)
        # thread_root is what lets a client rebuild the main flow client-side.
        self.assertEqual(
            by_body["the threaded reply"]["thread_root"], str(self.root.uuid)
        )
