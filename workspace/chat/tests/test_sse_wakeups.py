"""What a chat wake-up costs the stream of each recipient, end to end.

A notification goes through the real ``notify_sse`` and the real process hub
into the Pub/Sub stream loop of the recipient, which polls a real
``ChatSSEProvider``. Only Redis is faked: a publish is dispatched to the hub
in the same thread.
"""

import threading
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services.call_signaling import send_signal
from workspace.chat.services.posting import post_message
from workspace.chat.sse_provider import ChatSSEProvider
from workspace.core.sse_hub import SSEHub
from workspace.core.views import sse

User = get_user_model()


class ChatWakeUpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        for user in (self.alice, self.bob):
            ConversationMember.objects.create(conversation=self.conv, user=user)

        # Dispatch is driven by the fake publish: no listener thread.
        patcher = patch.object(threading.Thread, "start")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hub = SSEHub(redis_factory=None)
        self.bobs_tab = self.hub.subscribe(self.bob.id)

        redis = MagicMock()
        redis.publish.side_effect = lambda channel, data: self.hub._dispatch(
            {"channel": channel.encode(), "data": data}
        )
        patcher = patch("workspace.core.sse_registry._get_redis", return_value=redis)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Built ahead: opening a stream costs queries of its own.
        self.provider = ChatSSEProvider(self.bob, None)
        self.provider.get_initial_events = list

    def tearDown(self):
        cache.clear()

    def _bobs_stream(self):
        """One pass of Bob's stream loop, as the SSE lines it writes."""
        hub = MagicMock()
        hub.subscribe.return_value = self.bobs_tab
        request = RequestFactory().get("/api/v1/events")
        request.user = self.bob
        clock = iter([0.0, 0.0])
        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", 1),
            patch.object(sse, "_init_providers", return_value={"chat": self.provider}),
            patch.object(sse.time, "monotonic", lambda: next(clock, 999.0)),
        ):
            return "".join(sse._event_stream_pubsub(request, hub))

    def test_a_typing_ping_reaches_the_other_member_without_the_chat_queries(self):
        client = APIClient()
        client.force_authenticate(self.alice)
        client.post(reverse("chat-typing", args=[self.conv.pk]))

        with self.assertNumQueries(0):
            stream = self._bobs_stream()

        self.assertIn('"event":"chat.typing"', stream)
        self.assertIn('"display_name":"alice"', stream)

    def test_a_call_signal_reaches_its_peer_without_the_chat_queries(self):
        send_signal(uuid4(), self.bob.id, self.alice.id, {"type": "candidate"})

        with self.assertNumQueries(0):
            stream = self._bobs_stream()

        self.assertIn('"event":"chat.call_signal"', stream)

    def test_a_message_still_reaches_the_other_member(self):
        with self.captureOnCommitCallbacks(execute=True):
            message = post_message(self.conv, self.alice, "hello")

        stream = self._bobs_stream()

        self.assertIn('"event":"chat.message"', stream)
        self.assertIn(str(message.uuid), stream)

    def test_a_message_sent_while_someone_types_is_not_held_back(self):
        """Both wake-ups coalesce before the stream looks: the dirty one wins."""
        client = APIClient()
        client.force_authenticate(self.alice)
        with self.captureOnCommitCallbacks(execute=True):
            message = post_message(self.conv, self.alice, "hello")
        client.post(reverse("chat-typing", args=[self.conv.pk]))

        stream = self._bobs_stream()

        self.assertIn(str(message.uuid), stream)
        self.assertIn('"event":"chat.typing"', stream)
