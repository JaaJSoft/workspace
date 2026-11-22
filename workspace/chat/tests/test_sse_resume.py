"""A chat stream that follows another one resumes where the first stood.

The stream of a page is ended on purpose every ten minutes, and the page opens
the next one without re-syncing anything (``core/views/sse.py``). What the
first provider did not send, the second has to, and nothing a second time.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from workspace.chat.models import Conversation, ConversationMember, Message, Reaction
from workspace.chat.sse_provider import ChatSSEProvider
from workspace.core.views import sse

User = get_user_model()

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


class ChatFixture(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(username="viewer", password="pw")
        self.peer = User.objects.create_user(username="peer", password="pw")
        self.conv = self._conversation(self.viewer, self.peer)

    def tearDown(self):
        cache.clear()

    @staticmethod
    def _conversation(*members):
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=members[0]
        )
        for member in members:
            ConversationMember.objects.create(conversation=conv, user=member)
        return conv

    def _say(self, body, conv=None):
        return Message.objects.create(
            conversation=conv or self.conv, author=self.peer, body=body
        )


class ResumedProviderTests(ChatFixture):
    def setUp(self):
        # Ahead of any write: rows are stamped with this clock.
        self.now = T0
        clock = patch("django.utils.timezone.now", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()

    def _later(self, **delta):
        self.now += timedelta(**delta)

    def _stream(self, place=None):
        return ChatSSEProvider(self.viewer, place)

    @staticmethod
    def _sent(provider, name):
        """What a notified provider sends of one kind, with the id of each."""
        return [
            (data, event_id)
            for event, data, event_id in provider.poll("dirty")
            if event == name
        ]

    def _bodies(self, provider):
        return [data["message"]["body"] for data, _ in self._sent(provider, "message")]

    def test_a_message_written_between_two_streams_is_sent_by_the_second(self):
        first = self._stream()
        self._later(seconds=30)
        self._say("during the first")
        self._later(seconds=1)
        self.assertEqual(self._bodies(first), ["during the first"])
        place = first.resume_point()

        self._later(seconds=1)
        self._say("between the two")
        self._later(seconds=1)

        self.assertEqual(self._bodies(self._stream(place)), ["between the two"])

    def test_the_id_of_a_message_is_where_the_stream_stands_once_it_is_sent(self):
        first = self._stream()
        for body in ("one", "two"):
            self._later(seconds=1)
            self._say(body)
        self._later(seconds=1)
        (_, after_one), _ = self._sent(first, "message")

        self.assertEqual(self._bodies(self._stream(after_one)), ["two"])

    def test_a_change_the_first_stream_sent_is_not_sent_again(self):
        """No message came since: looked for from the last one on, as within a
        stream, the reaction would be sent again by every stream that follows."""
        message = self._say("hello")
        self._later(seconds=1)
        first = self._stream()
        self._later(seconds=30)
        Reaction.objects.create(message=message, user=self.peer, emoji="+1")
        self._later(seconds=10)
        self.assertEqual(len(self._sent(first, "reaction")), 1)
        place = first.resume_point()

        self._later(seconds=10)

        self.assertEqual(self._sent(self._stream(place), "reaction"), [])

    def test_a_change_made_between_two_streams_is_sent_by_the_second(self):
        message = self._say("hello")
        self._later(seconds=1)
        first = self._stream()
        self._later(seconds=30)
        self.assertEqual(self._sent(first, "reaction"), [])
        place = first.resume_point()

        self._later(seconds=1)
        Reaction.objects.create(message=message, user=self.peer, emoji="+1")
        self._later(seconds=1)

        self.assertEqual(len(self._sent(self._stream(place), "reaction")), 1)

    def test_a_read_between_two_streams_is_sent_by_the_second(self):
        place = self._stream().resume_point()

        self._later(seconds=5)
        ConversationMember.objects.filter(
            conversation=self.conv, user=self.peer
        ).update(last_read_at=self.now)
        self._later(seconds=5)

        self.assertEqual(len(self._sent(self._stream(place), "read")), 1)

    def test_a_conversation_joined_since_does_not_hand_over_its_history(self):
        """A stream that sent no message for hours still stands at its start.
        The one that follows looks no further back than a minute before the
        first last looked."""
        first = self._stream()
        self._later(hours=2)
        elsewhere = self._conversation(self.peer)
        self._say("said before the viewer came", conv=elsewhere)
        self._later(hours=2)
        self.assertEqual(self._bodies(first), [])
        place = first.resume_point()

        ConversationMember.objects.create(conversation=elsewhere, user=self.viewer)
        self._later(seconds=5)
        second = self._stream(place)
        self.assertEqual(self._bodies(second), [])

        self._later(seconds=1)
        self._say("said since", conv=elsewhere)
        self._later(seconds=1)
        self.assertEqual(self._bodies(second), ["said since"])

    def test_a_stream_resumed_in_the_middle_of_a_backlog_goes_on_with_it(self):
        """One poll sends fifty messages at most. Taken for a look that left
        nothing behind, it would have the next stream step over the rest."""
        first = self._stream()
        for number in range(60):
            self._later(seconds=2)
            self._say(f"message {number}")
        self._later(minutes=10)
        self.assertEqual(
            self._bodies(first), [f"message {number}" for number in range(50)]
        )

        second = self._stream(first.resume_point())

        self.assertEqual(
            self._bodies(second), [f"message {number}" for number in range(50, 60)]
        )

    def test_anything_but_a_place_starts_from_now(self):
        self._say("already there")
        self._later(minutes=5)
        naive = "2026-03-02T08:00:00"
        for raw in (
            None,
            "",
            "not-a-place",
            str(uuid.uuid4()),
            f"{naive}|{naive}",
            f"{T0.isoformat()}|",
            f"{T0.isoformat()}|{T0.isoformat()}|{T0.isoformat()}",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self._bodies(self._stream(raw)), [])

    def test_a_place_ahead_of_the_clock_is_taken_for_now(self):
        """Nothing written until then would be sent."""
        message = self._say("hello")
        ahead = (self.now + timedelta(hours=1)).isoformat()
        provider = self._stream(f"{ahead}|{ahead}")

        self._later(seconds=1)
        self._say("written since")
        Reaction.objects.create(message=message, user=self.peer, emoji="+1")
        self._later(seconds=1)

        sent = [name for name, _, _ in provider.poll("dirty")]
        self.assertEqual((sent.count("message"), sent.count("reaction")), (1, 1))


class RotatedStreamTests(ChatFixture):
    """Through the stream itself: the page only ever holds the id it was sent."""

    def _stream(self, place=None):
        """What a stream writes when its time is up as soon as it starts."""
        request = RequestFactory().get(
            "/api/v1/stream", {"last_event_id": place} if place else {}
        )
        request.user = self.viewer
        hub = MagicMock()
        hub.subscribe.return_value.wait.return_value = {}
        with patch.object(sse, "_MAX_CONNECTION_SECONDS", -1):
            return list(sse._event_stream_pubsub(request, hub))

    @staticmethod
    def _id(chunk):
        return next(line[4:] for line in chunk.split("\n") if line.startswith("id: "))

    def test_a_message_written_between_two_streams_reaches_the_page_once(self):
        first = self._stream()
        message = self._say("between the two")

        second = self._stream(self._id(first[-1]))
        third = self._stream(self._id(second[-1]))

        def carrying(chunks):
            return sum(str(message.uuid) in chunk for chunk in chunks)

        self.assertEqual(
            (carrying(first), carrying(second), carrying(third)), (0, 1, 0)
        )
