"""Regression test: a Last-Event-ID must not leak timing info across conversations."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.sse_provider import ChatSSEProvider
from workspace.chat.tests.test_chat import ChatTestMixin

User = get_user_model()


class SSELastEventIDCrossConversationTests(ChatTestMixin, TestCase):
    """ChatSSEProvider must not trust a Last-Event-ID from a foreign conversation.

    A foreign message UUID used to leak its ``created_at`` into ``self._since``,
    which then suppressed replay of the user's own messages older than that
    timestamp - a cross-conversation timing oracle. The fix scopes the lookup
    to ``self._member_conv_ids`` so a foreign UUID falls back to ``timezone.now()``.
    """

    def test_foreign_last_event_id_falls_back_to_now(self):
        # Foreign conversation creator is NOT a member of, with one message
        # whose created_at is far in the past so the assertion is unambiguous.
        foreign_conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Outsiders Only",
            created_by=self.outsider,
        )
        ConversationMember.objects.create(
            conversation=foreign_conv,
            user=self.outsider,
        )
        foreign_msg = Message.objects.create(
            conversation=foreign_conv,
            author=self.outsider,
            body="foreign-secret",
        )
        Message.objects.filter(pk=foreign_msg.pk).update(
            created_at="2020-01-01T00:00:00Z",
        )

        before_construct = timezone.now()
        provider = ChatSSEProvider(self.creator, last_event_id=str(foreign_msg.pk))

        # With the bug: provider._since == 2020-01-01 (foreign created_at).
        # With the fix: provider._since >= before_construct (timezone.now() fallback).
        self.assertGreaterEqual(provider._since, before_construct)
