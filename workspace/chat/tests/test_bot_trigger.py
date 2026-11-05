from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient, APITestCase

from workspace.ai.models import AITask, BotProfile
from workspace.ai.sse_provider import AIStreamSSEProvider
from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


@patch("workspace.chat.services.notifications.notify_sse")
@patch("workspace.notifications.tasks.send_push_notification.delay")
@patch("workspace.ai.tasks.chat.generate_chat_response.delay")
class BotReplyDispatchTests(APITestCase):
    """A message to a bot records the reply as under way before it is queued.

    The worker used to create the row when it started, so while the job
    waited in the queue nothing said a reply was coming: a stream opening in
    that window announced no generation and lowered the typing bubble the
    sender had just raised.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="erin", password="pw")
        self.bot = User.objects.create_user(username="helper", password="pw")
        BotProfile.objects.create(user=self.bot, system_prompt="x", is_public=True)
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        for member in (self.user, self.bot):
            ConversationMember.objects.create(
                conversation=self.conversation, user=member
            )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/v1/chat/conversations/{self.conversation.uuid}/messages"

    def tearDown(self):
        cache.clear()

    def _send(self):
        return self.client.post(self.url, {"body": "hello"}, format="json")

    def test_the_reply_is_pending_before_the_worker_picks_it_up(
        self, mock_delay, _push, _sse
    ):
        resp = self._send()

        self.assertEqual(resp.status_code, 201)
        task = AITask.objects.get(owner=self.bot, task_type=AITask.TaskType.CHAT)
        self.assertEqual(task.status, AITask.Status.PENDING)
        self.assertEqual(
            task.input_data,
            {
                "conversation_id": str(self.conversation.uuid),
                "message_id": resp.json()["uuid"],
            },
        )
        mock_delay.assert_called_once_with(str(task.uuid))

    def test_a_stream_opening_while_the_reply_is_queued_announces_it(
        self, _delay, _push, _sse
    ):
        self._send()

        events = AIStreamSSEProvider(self.user, None).get_initial_events()

        self.assertEqual(
            events[0],
            (
                "bot_generating",
                {"conversation_ids": [str(self.conversation.uuid)]},
                None,
            ),
        )

    def test_a_reply_that_cannot_be_queued_is_not_left_under_way(
        self, mock_delay, _push, _sse
    ):
        mock_delay.side_effect = ConnectionError("broker down")

        resp = self._send()

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Message.objects.filter(author=self.user).exists())
        task = AITask.objects.get(owner=self.bot)
        self.assertEqual(task.status, AITask.Status.FAILED)
        self.assertIsNotNone(task.completed_at)

    def test_no_bot_no_task(self, mock_delay, _push, _sse):
        ConversationMember.objects.filter(user=self.bot).delete()

        self._send()

        self.assertFalse(AITask.objects.exists())
        mock_delay.assert_not_called()
