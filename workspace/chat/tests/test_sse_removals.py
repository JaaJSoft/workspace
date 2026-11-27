"""Removals that delete their row still reach the other members' streams.

A removed reaction and an unpinned message leave nothing behind for the
stream's database poll to find, so the view queues them as discrete events.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    PinnedMessage,
    Reaction,
)
from workspace.chat.sse_provider import ChatSSEProvider

User = get_user_model()


class RemovalEventsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")
        self.carol = User.objects.create_user(username="carol", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)
        self.message = Message.objects.create(
            conversation=self.conv, author=self.bob, body="Hello"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.alice)

    def tearDown(self):
        cache.clear()

    def _events(self, provider, name):
        return [data for event, data, _ in provider.poll("dirty") if event == name]

    def _remove_reaction(self):
        Reaction.objects.create(message=self.message, user=self.alice, emoji="👍")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/api/v1/chat/messages/{self.message.uuid}/reactions",
                {"emoji": "👍"},
                format="json",
            )
        self.assertEqual(response.json()["action"], "removed")

    def _unpin(self):
        PinnedMessage.objects.create(
            conversation=self.conv, message=self.message, pinned_by=self.alice
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(
                f"/api/v1/chat/messages/{self.message.uuid}/pin"
            )
        self.assertEqual(response.status_code, 204)

    def test_another_member_receives_a_removed_reaction(self):
        stream = ChatSSEProvider(self.bob, last_event_id=None)

        self._remove_reaction()

        self.assertEqual(
            self._events(stream, "reaction"),
            [
                {
                    "type": "reaction",
                    "conversation_id": str(self.conv.uuid),
                    "message_id": str(self.message.uuid),
                    "emoji": "👍",
                    "user": {"id": self.alice.id, "username": "alice"},
                    "action": "removed",
                }
            ],
        )

    def test_another_member_receives_an_unpin(self):
        stream = ChatSSEProvider(self.bob, last_event_id=None)

        self._unpin()

        self.assertEqual(
            self._events(stream, "message_unpinned"),
            [
                {
                    "type": "message_unpinned",
                    "conversation_id": str(self.conv.uuid),
                    "message_id": str(self.message.uuid),
                    "unpinned_by": {"id": self.alice.id, "username": "alice"},
                }
            ],
        )

    def test_a_removal_is_delivered_once(self):
        stream = ChatSSEProvider(self.bob, last_event_id=None)
        self._unpin()

        self._events(stream, "message_unpinned")

        self.assertEqual(self._events(stream, "message_unpinned"), [])

    def test_every_stream_of_the_member_receives_it(self):
        first = ChatSSEProvider(self.bob, last_event_id=None)
        second = ChatSSEProvider(self.bob, last_event_id=None)

        self._remove_reaction()

        self.assertEqual(len(self._events(first, "reaction")), 1)
        self.assertEqual(len(self._events(second, "reaction")), 1)

    def test_the_author_and_outsiders_receive_nothing(self):
        own = ChatSSEProvider(self.alice, last_event_id=None)
        outsider = ChatSSEProvider(self.carol, last_event_id=None)

        self._remove_reaction()
        self._unpin()

        for stream in (own, outsider):
            events = [event for event, _, _ in stream.poll("dirty")]
            self.assertNotIn("reaction", events)
            self.assertNotIn("message_unpinned", events)

    def test_a_member_who_left_receives_nothing(self):
        stream = ChatSSEProvider(self.bob, last_event_id=None)
        ConversationMember.objects.filter(conversation=self.conv, user=self.bob).update(
            left_at=self.message.created_at
        )

        self._unpin()

        self.assertEqual(self._events(stream, "message_unpinned"), [])

    def test_a_stream_opened_after_the_removal_does_not_replay_it(self):
        # The page it belongs to was rendered from the database after the row
        # was gone.
        self._unpin()

        stream = ChatSSEProvider(self.bob, last_event_id=None)

        self.assertEqual(self._events(stream, "message_unpinned"), [])
