from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from pydantic import ValidationError

from workspace.chat.ai_tools import (
    AskUserQuestionParams,
    ChatToolProvider,
    SearchMessagesParams,
)
from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.users.services.settings import set_setting

User = get_user_model()


class AskUserQuestionToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pw",
        )
        self.bot = User.objects.create_user(
            username="bot",
            email="b@test.com",
            password="pw",
        )
        self.provider = ChatToolProvider()

    def _run(self, question="Pick one", options=None, context=None):
        opts = options if options is not None else ["Yes", "No"]
        args = AskUserQuestionParams(question=question, options=opts)
        ctx = context if context is not None else {}
        result = self.provider.ask_user_question(
            args,
            user=self.user,
            bot=self.bot,
            conversation_id=None,
            context=ctx,
        )
        return result, ctx

    def test_nominal_writes_context_and_sets_stop_flag(self):
        result, ctx = self._run("Tone?", ["Formal", "Casual"])
        self.assertNotIn("Error", result)
        self.assertEqual(ctx["question"]["question"], "Tone?")
        self.assertEqual(ctx["question"]["options"], ["Formal", "Casual"])
        self.assertTrue(ctx["stop_after_round"])

    def test_dedupes_and_trims_options(self):
        result, ctx = self._run("Q", ["  Yes ", "No", " Yes ", "no"])
        self.assertEqual(ctx["question"]["options"], ["Yes", "No", "no"])

    def test_caps_options_at_six(self):
        opts = ["A", "B", "C", "D", "E", "F"]
        result, ctx = self._run("Q", opts)
        self.assertEqual(len(ctx["question"]["options"]), 6)

    def test_setdefault_keeps_first_question(self):
        ctx = {}
        self._run("First?", ["A", "B"], context=ctx)
        self._run("Second?", ["X", "Y"], context=ctx)
        self.assertEqual(ctx["question"]["question"], "First?")

    def test_fewer_than_two_options_returns_error(self):
        args = AskUserQuestionParams(question="Q", options=["", "   "])
        ctx = {}
        result = self.provider.ask_user_question(
            args,
            user=self.user,
            bot=self.bot,
            conversation_id=None,
            context=ctx,
        )
        self.assertIn("Error", result)
        self.assertNotIn("question", ctx)
        self.assertNotIn("stop_after_round", ctx)

    def test_pydantic_rejects_one_option(self):
        with self.assertRaises(ValidationError):
            AskUserQuestionParams(question="Q", options=["Only one"])

    def test_pydantic_rejects_seven_options(self):
        with self.assertRaises(ValidationError):
            AskUserQuestionParams(
                question="Q",
                options=["1", "2", "3", "4", "5", "6", "7"],
            )

    def test_pydantic_rejects_empty_question(self):
        with self.assertRaises(ValidationError):
            AskUserQuestionParams(question="", options=["A", "B"])

    def test_tool_rejects_whitespace_only_question(self):
        args = AskUserQuestionParams(question="   ", options=["A", "B"])
        ctx = {}
        result = self.provider.ask_user_question(
            args,
            user=self.user,
            bot=self.bot,
            conversation_id=None,
            context=ctx,
        )
        self.assertIn("Error", result)
        self.assertNotIn("question", ctx)
        self.assertNotIn("stop_after_round", ctx)


class SearchMessagesTimezoneTests(TestCase):
    """Tools run in Celery with no active timezone: they must resolve the
    user's stored zone explicitly instead of relying on the middleware."""

    def setUp(self):
        self.user = User.objects.create_user(username="tzsm", password="pw")
        self.bot = User.objects.create_user(username="tzbot", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="G", created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.provider = ChatToolProvider()

    def tearDown(self):
        cache.clear()

    def _search(self, **kwargs):
        args = SearchMessagesParams(query="boundary", **kwargs)
        return self.provider.search_messages(
            args, user=self.user, bot=self.bot, conversation_id=None, context={}
        )

    def _make_message(self, created_at):
        msg = Message.objects.create(
            conversation=self.conv, author=self.user, body="boundary msg"
        )
        Message.objects.filter(pk=msg.pk).update(created_at=created_at)
        return msg

    def test_today_range_uses_stored_user_timezone(self):
        # 22:30 UTC Jan 31 = 23:30 Jan 31 in Paris; at 23:45 UTC the user's
        # day is already Feb 1, so "today" must exclude this message.
        self._make_message(datetime(2026, 1, 31, 22, 30, tzinfo=UTC))
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        fixed_now = datetime(2026, 1, 31, 23, 45, tzinfo=UTC)
        with patch("django.utils.timezone.now", return_value=fixed_now):
            result = self._search(date_range="today")
        self.assertIn("No messages found", result)

    def test_timestamps_rendered_in_user_timezone(self):
        self._make_message(datetime(2026, 1, 31, 23, 30, tzinfo=UTC))
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        result = self._search()
        self.assertIn("2026-02-01 00:30", result)
