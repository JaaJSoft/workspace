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

    def test_the_api_message_list_hides_them_too(self):
        api = APIClient()
        api.force_authenticate(user=self.alice)
        url = reverse(
            "chat-messages", kwargs={"conversation_id": self.conversation.uuid}
        )
        bodies = [m["body"] for m in api.get(url).data["messages"]]
        self.assertEqual(bodies, ["the root message"])

    def test_the_api_message_list_honours_the_preference(self):
        set_setting(
            self.alice, "chat", "preferences", {"showThreadRepliesInline": True}
        )
        api = APIClient()
        api.force_authenticate(user=self.alice)
        url = reverse(
            "chat-messages", kwargs={"conversation_id": self.conversation.uuid}
        )
        bodies = [m["body"] for m in api.get(url).data["messages"]]
        self.assertEqual(bodies, ["the root message", "the threaded reply"])
