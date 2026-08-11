"""Side effects of posting a chat message, per entry point.

Posting a message is five effects, not one: the row, the unread counters,
the conversation bump, the live SSE fan-out and the notification pipeline.
These tests pin the full set down at each entry point so no site can quietly
implement four out of five again.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient, APITestCase

from workspace.ai.models import AITask, BotProfile
from workspace.ai.services.responses import post_bot_message
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageInteraction,
)
from workspace.notifications.models import Notification

User = get_user_model()


class MessagePostingMixin:
    """A group with an author, two human members and a bot."""

    def setUp(self):
        super().setUp()
        self.author = User.objects.create_user(username="author", password="p")
        self.alice = User.objects.create_user(username="alice", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.bot = User.objects.create_user(username="bot", password="p")
        BotProfile.objects.create(user=self.bot)

        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Team",
            created_by=self.author,
        )
        for u in (self.author, self.alice, self.bob, self.bot):
            ConversationMember.objects.create(conversation=self.conv, user=u)

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()
        super().tearDown()

    def _unread(self, user):
        return ConversationMember.objects.get(
            conversation=self.conv,
            user=user,
        ).unread_count

    def _updated_at(self):
        return Conversation.objects.get(pk=self.conv.pk).updated_at

    def _sse_targets(self, mock_sse):
        return {call.args[1] for call in mock_sse.call_args_list}

    def _notified(self):
        return set(
            Notification.objects.filter(origin="chat").values_list(
                "recipient_id", flat=True
            )
        )


@patch("workspace.chat.services.notifications.notify_sse")
@patch("workspace.notifications.tasks.send_push_notification.delay")
class SendMessageEndpointTests(MessagePostingMixin, APITestCase):
    """POST /api/v1/chat/conversations/<uuid>/messages"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.author)
        self.url = f"/api/v1/chat/conversations/{self.conv.uuid}/messages"

    def _send(self, body="hello"):
        # The bot member would otherwise answer eagerly (Celery runs inline in
        # tests), fail on the missing API key, and post an error message whose
        # own side effects would land in these assertions. views_messages
        # imports the trigger lazily, so the definition site is the one to
        # patch; views_interactions binds it at import time and needs its own.
        with self.captureOnCommitCallbacks(execute=True):
            with patch("workspace.chat.views._trigger_bot_response"):
                return self.client.post(self.url, {"body": body}, format="json")

    def test_increments_unread_for_others_and_not_the_author(self, _push, _sse):
        self._send()

        self.assertEqual(self._unread(self.alice), 1)
        self.assertEqual(self._unread(self.bob), 1)
        self.assertEqual(self._unread(self.author), 0)

    def test_bumps_the_conversation(self, _push, _sse):
        before = self._updated_at()
        self._send()
        self.assertGreater(self._updated_at(), before)

    def test_fans_out_sse_to_the_other_members(self, _push, mock_sse):
        self._send()
        self.assertIn(self.alice.id, self._sse_targets(mock_sse))
        self.assertIn(self.bob.id, self._sse_targets(mock_sse))
        self.assertNotIn(self.author.id, self._sse_targets(mock_sse))

    def test_notifies_the_other_human_members(self, mock_push, _sse):
        self._send()

        self.assertEqual(self._notified(), {self.alice.id, self.bob.id})
        self.assertEqual(mock_push.call_count, 2)


@patch("workspace.chat.services.notifications.notify_sse")
@patch("workspace.notifications.tasks.send_push_notification.delay")
class BotReplyTests(MessagePostingMixin, TestCase):
    """workspace.ai.services.responses.post_bot_message"""

    def setUp(self):
        super().setUp()
        self.ai_task = AITask.objects.create(
            owner=self.author,
            task_type=AITask.TaskType.CHAT,
        )

    def _reply(self, content="here you go"):
        with self.captureOnCommitCallbacks(execute=True):
            return post_bot_message(
                conversation=self.conv,
                bot_user=self.bot,
                result={
                    "content": content,
                    "model": "test",
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                },
                tool_context={},
                ai_task=self.ai_task,
            )

    def test_increments_unread_for_everyone_but_the_bot(self, _push, _sse):
        self._reply()

        self.assertEqual(self._unread(self.author), 1)
        self.assertEqual(self._unread(self.alice), 1)
        self.assertEqual(self._unread(self.bot), 0)

    def test_bumps_the_conversation(self, _push, _sse):
        before = self._updated_at()
        self._reply()
        self.assertGreater(self._updated_at(), before)

    def test_fans_out_sse_to_the_humans(self, _push, mock_sse):
        self._reply()
        self.assertIn(self.author.id, self._sse_targets(mock_sse))
        self.assertNotIn(self.bot.id, self._sse_targets(mock_sse))

    def test_notifies_the_humans(self, mock_push, _sse):
        self._reply()

        self.assertEqual(
            self._notified(),
            {self.author.id, self.alice.id, self.bob.id},
        )
        self.assertEqual(mock_push.call_count, 3)


@patch("workspace.chat.services.notifications.notify_sse")
@patch("workspace.notifications.tasks.send_push_notification.delay")
class InteractionAnswerTests(MessagePostingMixin, APITestCase):
    """POST /api/v1/chat/messages/<uuid>/answer

    Clicking a suggested option posts a real message into the conversation,
    so it owes the same five effects as any other message. It used to create
    the row and stop there.
    """

    def setUp(self):
        super().setUp()
        self.question = Message.objects.create(
            conversation=self.conv,
            author=self.bot,
            body="Which tone?",
        )
        MessageInteraction.objects.create(
            message=self.question,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Which tone?", "options": ["Formal", "Casual"]},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.author)
        self.url = f"/api/v1/chat/messages/{self.question.uuid}/answer"

    def _answer(self):
        with self.captureOnCommitCallbacks(execute=True):
            with patch("workspace.chat.views_interactions._trigger_bot_response"):
                return self.client.post(self.url, {"option_index": 0}, format="json")

    def test_posts_the_answer_as_a_message(self, _push, _sse):
        resp = self._answer()
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            Message.objects.filter(
                conversation=self.conv,
                author=self.author,
                body="Formal",
            ).exists()
        )

    def test_increments_unread_for_the_other_members(self, _push, _sse):
        self._answer()

        self.assertEqual(self._unread(self.alice), 1)
        self.assertEqual(self._unread(self.bob), 1)
        self.assertEqual(self._unread(self.author), 0)

    def test_bumps_the_conversation(self, _push, _sse):
        before = self._updated_at()
        self._answer()
        self.assertGreater(self._updated_at(), before)

    def test_fans_out_sse_to_the_other_members(self, _push, mock_sse):
        self._answer()
        self.assertIn(self.alice.id, self._sse_targets(mock_sse))
        self.assertNotIn(self.author.id, self._sse_targets(mock_sse))

    def test_notifies_the_other_human_members(self, mock_push, _sse):
        self._answer()

        self.assertEqual(self._notified(), {self.alice.id, self.bob.id})
        self.assertEqual(mock_push.call_count, 2)
