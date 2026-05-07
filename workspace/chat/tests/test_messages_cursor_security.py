"""Regression test: ?before=<uuid> cursor must not leak timing info across conversations.

Before the fix, ``conversation_messages_view`` resolved the cursor with an
unrestricted ``Message.objects.get(uuid=before_uuid)``. A caller could pass the
UUID of a message in a conversation they did not belong to and observe the
foreign message's ``created_at`` indirectly via the page boundary it produced
on the listing they *did* have access to. The fix scopes the lookup to the
current conversation, so a foreign UUID resolves to ``None`` and the cursor
filter is skipped (same behavior as a stale/deleted cursor).
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.tests.test_chat import ChatTestMixin

User = get_user_model()


class MessagesCursorCrossConversationTests(ChatTestMixin, APITestCase):
    """A ?before cursor pointing at a foreign conversation's message is ignored."""

    def setUp(self):
        super().setUp()

        # Three messages in self.group (the conversation creator can read).
        self.early_msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='early-in-group',
        )
        self.middle_msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='middle-in-group',
        )
        self.late_msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='late-in-group',
        )

        # A foreign conversation creator is NOT a member of, with one message
        # whose created_at falls between middle_msg and late_msg. If the cursor
        # lookup were unrestricted, ?before=<foreign> would clip late_msg out
        # of creator's listing - revealing the foreign message's timestamp.
        self.foreign_conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='Outsiders Only',
            created_by=self.outsider,
        )
        ConversationMember.objects.create(
            conversation=self.foreign_conv, user=self.outsider,
        )
        self.foreign_msg = Message.objects.create(
            conversation=self.foreign_conv, author=self.outsider, body='foreign-secret',
        )

        # Pin created_at ordering deterministically: early < middle < foreign < late.
        # auto_now_add prevents setting it via .save(), so use queryset .update().
        Message.objects.filter(pk=self.early_msg.pk).update(
            created_at='2024-01-01T10:00:00Z',
        )
        Message.objects.filter(pk=self.middle_msg.pk).update(
            created_at='2024-01-01T11:00:00Z',
        )
        Message.objects.filter(pk=self.foreign_msg.pk).update(
            created_at='2024-01-01T12:00:00Z',
        )
        Message.objects.filter(pk=self.late_msg.pk).update(
            created_at='2024-01-01T13:00:00Z',
        )

    def url(self):
        return f'/chat/{self.group.pk}/messages'

    def test_foreign_conversation_cursor_is_ignored(self):
        """?before=<foreign uuid> must not filter creator's listing (UI partial)."""
        self.client.force_login(self.creator)
        resp = self.client.get(self.url(), {'before': str(self.foreign_msg.pk)})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # late_msg would be clipped if the foreign cursor's created_at leaked
        # into the filter (created_at__lt=2024-01-01T12:00:00Z drops late_msg).
        self.assertIn('late-in-group', body)
        self.assertIn('middle-in-group', body)
        self.assertIn('early-in-group', body)

    def test_foreign_conversation_cursor_is_ignored_on_api(self):
        """?before=<foreign uuid> must not filter creator's listing (REST API)."""
        self.client.force_authenticate(self.creator)
        resp = self.client.get(
            f'/api/v1/chat/conversations/{self.group.pk}/messages',
            {'before': str(self.foreign_msg.pk)},
        )
        self.assertEqual(resp.status_code, 200)
        bodies = {m['body'] for m in resp.data['messages']}
        # All three in-conversation messages must be present. If the foreign
        # cursor's created_at had leaked, late-in-group (13:00) would be
        # filtered out by created_at__lt=12:00.
        self.assertEqual(
            bodies,
            {'early-in-group', 'middle-in-group', 'late-in-group'},
        )
