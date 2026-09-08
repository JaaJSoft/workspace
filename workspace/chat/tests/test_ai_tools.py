import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from pydantic import ValidationError

from workspace.ai.models import BotProfile, ConversationSummary
from workspace.chat.ai_tools import (
    READ_MAX_BODY_CHARS,
    READ_MAX_CHARS,
    READ_MAX_MESSAGES,
    AskUserQuestionParams,
    ChatToolProvider,
    ReadConversationParams,
    SearchMessagesParams,
    SummarizeConversationParams,
)
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)
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


BASE_TIME = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


class ConversationToolsTestCase(TestCase):
    """Shared fixture for read_conversation / summarize_conversation.

    The bot is deliberately *not* a member of ``self.conv``: access to a
    conversation is granted by the user's membership, never the bot's.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="reader", password="pw")
        self.bot = User.objects.create_user(username="readerbot", password="pw")
        BotProfile.objects.create(user=self.bot)
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Roadmap", created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.provider = ChatToolProvider()

    def tearDown(self):
        cache.clear()

    def _message(self, body, minutes, author=None, conversation=None):
        msg = Message.objects.create(
            conversation=conversation or self.conv,
            author=author or self.user,
            body=body,
        )
        Message.objects.filter(pk=msg.pk).update(
            created_at=BASE_TIME + timedelta(minutes=minutes)
        )
        msg.refresh_from_db()
        return msg

    def _read(self, conversation=None, **kwargs):
        args = ReadConversationParams(
            conversation_id=(conversation or self.conv).uuid, **kwargs
        )
        return self.provider.read_conversation(
            args, user=self.user, bot=self.bot, conversation_id=None, context={}
        )

    def _summarize(self, conversation=None):
        args = SummarizeConversationParams(
            conversation_id=(conversation or self.conv).uuid
        )
        return self.provider.summarize_conversation(
            args, user=self.user, bot=self.bot, conversation_id=None, context={}
        )


class ReadConversationToolTests(ConversationToolsTestCase):
    def test_returns_messages_oldest_first(self):
        self._message("second", 1)
        self._message("first", 0)

        payload = json.loads(self._read())

        self.assertEqual(payload["conversation"], "Roadmap")
        self.assertEqual([m["body"] for m in payload["messages"]], ["first", "second"])
        self.assertEqual(payload["messages"][0]["author"], "reader")
        self.assertEqual(payload["messages"][0]["timestamp"], "2026-03-01 09:00")
        self.assertFalse(payload["older_messages_omitted"])

    def test_bot_authored_messages_are_labelled(self):
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self._message("from the bot", 0, author=self.bot)

        payload = json.loads(self._read())

        self.assertEqual(payload["messages"][0]["author"], "[Bot] readerbot")

    def test_attachment_names_stand_in_for_an_empty_body(self):
        msg = self._message("", 0)
        MessageAttachment.objects.create(
            message=msg,
            file="chat/voice.webm",
            original_name="voice.webm",
            mime_type="audio/webm",
            size=42,
        )

        payload = json.loads(self._read())

        self.assertEqual(payload["messages"][0]["body"], "[attachment: voice.webm]")

    def test_deleted_messages_are_skipped(self):
        self._message("kept", 0)
        gone = self._message("gone", 1)
        Message.objects.filter(pk=gone.pk).update(deleted_at=BASE_TIME)

        payload = json.loads(self._read())

        self.assertEqual([m["body"] for m in payload["messages"]], ["kept"])

    def test_long_body_is_truncated(self):
        self._message("x" * (READ_MAX_BODY_CHARS + 500), 0)

        payload = json.loads(self._read())

        self.assertEqual(len(payload["messages"][0]["body"]), READ_MAX_BODY_CHARS + 1)
        self.assertTrue(payload["messages"][0]["body"].endswith("…"))

    def test_limit_is_clamped_to_the_hard_cap(self):
        for i in range(READ_MAX_MESSAGES + 2):
            self._message(f"m{i}", i)

        payload = json.loads(self._read(limit=999))

        self.assertEqual(len(payload["messages"]), READ_MAX_MESSAGES)
        # The newest survive, the oldest are the ones dropped.
        self.assertEqual(payload["messages"][-1]["body"], f"m{READ_MAX_MESSAGES + 1}")
        self.assertTrue(payload["older_messages_omitted"])

    def test_requested_limit_is_honoured_below_the_cap(self):
        for i in range(5):
            self._message(f"m{i}", i)

        payload = json.loads(self._read(limit=2))

        self.assertEqual([m["body"] for m in payload["messages"]], ["m3", "m4"])
        self.assertTrue(payload["older_messages_omitted"])

    def test_character_budget_drops_the_oldest_messages(self):
        # 20 x 1000 chars overshoots the budget: the tail must survive whole
        # and the head must be the part that falls off.
        for i in range(20):
            self._message(f"{i:03d}" + "x" * (READ_MAX_BODY_CHARS - 3), i)

        payload = json.loads(self._read(limit=20))

        bodies = [m["body"] for m in payload["messages"]]
        self.assertLess(len(bodies), 20)
        self.assertLessEqual(sum(len(b) for b in bodies), READ_MAX_CHARS)
        self.assertTrue(bodies[-1].startswith("019"))
        self.assertTrue(payload["older_messages_omitted"])

    def test_empty_conversation(self):
        self.assertIn("no messages", self._read())

    def test_conversation_the_user_never_joined_is_refused(self):
        other = User.objects.create_user(username="stranger", password="pw")
        foreign = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Secret", created_by=other
        )
        ConversationMember.objects.create(conversation=foreign, user=other)
        self._message("classified", 0, author=other, conversation=foreign)

        result = self._read(conversation=foreign)

        self.assertTrue(result.startswith("Error:"))
        self.assertNotIn("classified", result)

    def test_conversation_the_user_left_is_refused(self):
        self._message("hello", 0)
        ConversationMember.objects.filter(
            conversation=self.conv, user=self.user
        ).update(left_at=BASE_TIME)

        self.assertTrue(self._read().startswith("Error:"))


@override_settings(AI_CHAT_CONTEXT_SIZE=2)
class SummarizeConversationToolTests(ConversationToolsTestCase):
    def _llm(self, content="FRESH SUMMARY"):
        return {"content": content, "prompt_tokens": 1, "completion_tokens": 1}

    def _fill(self, count=5):
        for i in range(count):
            self._message(f"m{i}", i)

    def test_short_conversation_returns_the_transcript_without_a_model_call(self):
        self._message("hi", 0)
        self._message("there", 1)

        with patch("workspace.ai.services.chat_summary.call_llm") as llm:
            payload = json.loads(self._summarize())

        llm.assert_not_called()
        self.assertIn("Short conversation", payload["note"])
        self.assertEqual([m["body"] for m in payload["messages"]], ["hi", "there"])

    def test_current_summary_is_served_without_a_model_call(self):
        self._fill()
        with patch(
            "workspace.ai.services.chat_summary.call_llm",
            return_value=self._llm("STORED SUMMARY"),
        ):
            self._summarize()

        with patch("workspace.ai.services.chat_summary.call_llm") as llm:
            payload = json.loads(self._summarize())

        llm.assert_not_called()
        self.assertEqual(payload["summary"], "STORED SUMMARY")
        self.assertEqual(payload["conversation"], "Roadmap")
        self.assertIn("read_conversation", payload["covers"])

    def test_stale_summary_is_refreshed(self):
        self._fill()
        ConversationSummary.objects.create(
            conversation=self.conv,
            content="STALE",
            up_to=BASE_TIME - timedelta(hours=1),
        )

        with patch(
            "workspace.ai.services.chat_summary.call_llm", return_value=self._llm()
        ) as llm:
            payload = json.loads(self._summarize())

        llm.assert_called_once()
        self.assertEqual(payload["summary"], "FRESH SUMMARY")

    def test_model_failure_surfaces_as_an_error(self):
        self._fill()

        with patch(
            "workspace.ai.services.chat_summary.call_llm",
            side_effect=RuntimeError("backend down"),
        ):
            result = self._summarize()

        self.assertTrue(result.startswith("Error:"))
        self.assertIn("backend down", result)

    def test_conversation_the_user_never_joined_is_refused(self):
        other = User.objects.create_user(username="stranger", password="pw")
        foreign = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Secret", created_by=other
        )
        ConversationMember.objects.create(conversation=foreign, user=other)

        with patch("workspace.ai.services.chat_summary.call_llm") as llm:
            result = self._summarize(conversation=foreign)

        llm.assert_not_called()
        self.assertTrue(result.startswith("Error:"))
