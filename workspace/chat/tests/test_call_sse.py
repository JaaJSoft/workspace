from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services import call_signaling as sig
from workspace.chat.sse_provider import ChatSSEProvider


class CallSseDeliveryTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(username="u", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)

    def tearDown(self):
        cache.clear()

    def test_poll_emits_queued_call_events(self):
        sig.enqueue_event(self.user.id, "call_started", {"session_id": "s1"})
        provider = ChatSSEProvider(self.user, None)
        events = provider.poll("dirty")
        names = [e[0] for e in events]
        self.assertIn("call_started", names)

    def test_call_events_emitted_even_when_not_dirty(self):
        # Signaling latency matters: deliver on the None (timeout) poll too.
        sig.enqueue_event(self.user.id, "call_signal", {"from_user_id": 2})
        provider = ChatSSEProvider(self.user, None)
        events = provider.poll(None)
        names = [e[0] for e in events]
        self.assertIn("call_signal", names)

    def test_drained_events_are_not_re_emitted(self):
        sig.enqueue_event(self.user.id, "call_started", {})
        provider = ChatSSEProvider(self.user, None)
        provider.poll(None)
        self.assertEqual(provider.poll(None), [])
