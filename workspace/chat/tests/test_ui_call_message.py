from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.models import Conversation, Message
from workspace.chat.ui.views import group_messages


class SystemCallGroupingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )

    def test_system_message_is_its_own_group(self):
        Message.objects.create(conversation=self.conv, author=self.user, body="hello")
        Message.objects.create(
            conversation=self.conv,
            author=self.user,
            kind=Message.Kind.SYSTEM,
            body="Call started",
            tool_data={"type": "call", "state": "active"},
        )
        msgs = list(self.conv.messages.order_by("created_at"))
        groups = group_messages(msgs, self.user)
        types = [g["type"] for g in groups]
        self.assertIn("system", types)
        system_groups = [g for g in groups if g["type"] == "system"]
        self.assertEqual(system_groups[0]["message"].tool_data["type"], "call")
