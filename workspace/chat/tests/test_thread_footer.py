from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.rendering import render_message_body

User = get_user_model()


class ThreadFooterTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="secret")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice
        )
        self.client.force_login(self.alice)
        self.url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.conversation.uuid},
        )

    def tearDown(self):
        cache.clear()

    def _root(self, reply_count):
        # body_html too: the bubble only renders the rendered HTML, so a
        # message built from body alone would carry a footer under an empty
        # bubble - not the shape these tests mean to exercise.
        return Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="root",
            body_html=render_message_body("root"),
            reply_count=reply_count,
        )

    def test_a_message_carries_its_uuid_as_data(self):
        root = self._root(0)
        html = self.client.get(self.url).content.decode()
        self.assertIn(f'data-message-uuid="{root.uuid}"', html)

    def test_a_message_without_replies_shows_no_footer(self):
        self._root(0)
        html = self.client.get(self.url).content.decode()
        self.assertNotIn('data-testid="thread-footer"', html)

    def test_a_root_with_replies_offers_to_open_the_thread(self):
        root = self._root(3)
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-testid="thread-footer"', html)
        self.assertIn("3 replies", html)
        self.assertIn(f"openThread('{root.uuid}')", html)

    def test_a_single_reply_is_not_pluralised(self):
        self._root(1)
        html = self.client.get(self.url).content.decode()
        self.assertIn("1 reply", html)
        self.assertNotIn("1 replies", html)
