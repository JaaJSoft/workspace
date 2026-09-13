from django.contrib.auth import get_user_model
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.ai.models import AITask
from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.ui.templatetags.chat_tags import render_ai_usage

User = get_user_model()


class RenderAiUsageTagTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.bot = User.objects.create_user(username="bot", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        self.message = Message.objects.create(
            conversation=self.conv, author=self.bot, body="hi"
        )

    def _task(self, **fields):
        defaults = {
            "owner": self.user,
            "task_type": AITask.TaskType.CHAT,
            "status": AITask.Status.COMPLETED,
            "chat_message": self.message,
            "model_used": "gpt-x",
            "prompt_tokens": 1200,
            "completion_tokens": 340,
            "generation_seconds": 8.5,
            "answer_tokens": 300,
            "answer_seconds": 6.0,
        }
        return AITask.objects.create(**{**defaults, **fields})

    def test_message_without_a_task_renders_nothing(self):
        self.assertIsNone(render_ai_usage(self.message)["usage"])

    def test_completed_task_yields_model_tokens_time_and_speed(self):
        self._task()

        usage = render_ai_usage(self.message)["usage"]

        self.assertEqual(usage["model"], "gpt-x")
        self.assertEqual(usage["prompt_tokens"], 1200)
        self.assertEqual(usage["completion_tokens"], 340)
        self.assertEqual(usage["total_tokens"], 1540)
        self.assertEqual(usage["seconds"], 8.5)

    def test_speed_is_the_answer_call_not_the_whole_run(self):
        # 300 tokens in 6s for the answer; the run total (340 in 8.5s) also
        # carries the tool round, which read a long prompt for a few tokens.
        self._task()

        self.assertEqual(
            render_ai_usage(self.message)["usage"]["tokens_per_second"], 50.0
        )

    def test_pending_task_is_not_shown(self):
        self._task(status=AITask.Status.PROCESSING)

        self.assertIsNone(render_ai_usage(self.message)["usage"])

    def test_speed_needs_a_measured_duration(self):
        self._task(generation_seconds=None, answer_seconds=None)

        usage = render_ai_usage(self.message)["usage"]

        self.assertIsNone(usage["seconds"])
        self.assertIsNone(usage["tokens_per_second"])

    def test_backend_reporting_no_usage_still_shows_the_model(self):
        self._task(
            prompt_tokens=None,
            completion_tokens=None,
            generation_seconds=None,
            answer_tokens=None,
            answer_seconds=None,
        )

        usage = render_ai_usage(self.message)["usage"]

        self.assertEqual(usage["model"], "gpt-x")
        self.assertIsNone(usage["total_tokens"])
        self.assertIsNone(usage["tokens_per_second"])

    def test_partial_renders_compact_figures_with_exact_titles(self):
        self._task()

        html = render_to_string(
            "chat/ui/partials/_ai_usage.html", render_ai_usage(self.message)
        )

        self.assertIn('data-lucide="info"', html)
        self.assertIn("gpt-x", html)
        self.assertIn("1.2k", html)
        self.assertIn('title="1200 tokens"', html)
        self.assertIn("340", html)
        self.assertIn("8.5s", html)
        self.assertIn("50 tok/s", html)


class ConversationMessagesUsageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.bot = User.objects.create_user(username="bot", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.client.force_login(self.user)
        self.url = reverse(
            "chat_ui:conversation_messages", kwargs={"conversation_uuid": self.conv.pk}
        )

    def _bot_reply(self, body="hi"):
        message = Message.objects.create(
            conversation=self.conv, author=self.bot, body=body, body_html=body
        )
        AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CHAT,
            status=AITask.Status.COMPLETED,
            chat_message=message,
            model_used="gpt-x",
            prompt_tokens=1200,
            completion_tokens=340,
            generation_seconds=8.5,
            answer_tokens=300,
            answer_seconds=6.0,
        )
        return message

    def test_bot_reply_carries_its_usage_popover(self):
        self._bot_reply()

        html = self.client.get(self.url).content.decode()

        self.assertIn('data-lucide="info"', html)
        self.assertIn("50 tok/s", html)

    def test_human_message_carries_none(self):
        Message.objects.create(
            conversation=self.conv, author=self.user, body="hello", body_html="hello"
        )

        html = self.client.get(self.url).content.decode()

        self.assertNotIn('data-lucide="info"', html)

    def test_usage_is_loaded_once_for_the_whole_page(self):
        self._bot_reply("one")
        with CaptureQueriesContext(connection) as single:
            self.client.get(self.url)

        self._bot_reply("two")
        self._bot_reply("three")
        with CaptureQueriesContext(connection) as several:
            self.client.get(self.url)

        self.assertEqual(len(several), len(single))
