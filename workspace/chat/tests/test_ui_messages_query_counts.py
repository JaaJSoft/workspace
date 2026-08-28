from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


class ReadReceiptQueryCountTests(TestCase):
    """Read receipts need every recipient's ``last_read_at`` plus their
    count; both come out of the same rows, so one query has to serve both."""

    def setUp(self):
        self.author = User.objects.create_user(username="author", password="pw")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="G", created_by=self.author
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.author
        )
        for i in range(4):
            reader = User.objects.create_user(username=f"reader{i}", password="pw")
            ConversationMember.objects.create(
                conversation=self.conversation, user=reader
            )
        for i in range(3):
            Message.objects.create(
                conversation=self.conversation, author=self.author, body=f"m{i}"
            )
        self.url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.conversation.uuid},
        )
        self.client.force_login(self.author)

    def test_recipients_are_read_in_a_single_query(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        member_queries = [
            q
            for q in ctx.captured_queries
            if 'FROM "chat_conversationmember"' in q["sql"]
        ]
        # 1 = the caller's access check. The read-receipt block adds exactly
        # one more; counting the recipients separately would make it two.
        self.assertEqual(
            len(member_queries),
            2,
            f"expected 2 membership queries, got {len(member_queries)}",
        )

    def test_recipient_total_still_counts_every_member_but_the_author(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        rendered = [
            message
            for group in response.context["groups"]
            if group["type"] == "messages"
            for message in group["messages"]
        ]
        self.assertEqual(len(rendered), 3)
        for message in rendered:
            self.assertEqual(message.total_recipients, 4)
            self.assertEqual(message.read_count, 0)
            self.assertFalse(message.all_read)
