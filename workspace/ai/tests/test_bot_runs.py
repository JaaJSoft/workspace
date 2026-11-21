"""A bot reply on its Pydantic AI agent: when it stops, and what it leaves.

The model is scripted and the tools are a registry of this file's own, so
each test says which replies the model gives and reads what the run made
of them. Handlers run for real, on the thread the run gives them.
"""

import threading
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
)

from workspace.ai.models import AITask, BotProfile, UserMemory
from workspace.ai.services.bot_runs import StopReason
from workspace.ai.tasks.chat import generate_chat_response
from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.users.services.settings import set_setting

from .agents import (
    BotRunCase,
    RecordingTools,
    call,
    reply,
    scripted,
    tool_results,
)
from .agents import (
    read_call as _read,
)
from .agents import (
    write_call as _write,
)
from .chat_tasks import queue_chat_task

User = get_user_model()


class AnsweredTests(BotRunCase):
    def test_a_reply_without_tools_ends_the_run_at_once(self):
        run = self.run_bot(reply("hello"))

        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(run.output, "hello")
        self.assertIsNone(run.tool_data)
        self.assertEqual(len(self.script.requests), 1)
        self.assertEqual(
            self.script.tools,
            ["ask_user", "explode", "fail", "read_webpage", "show_image", "write_note"],
        )

    def test_the_conversation_carries_each_round(self):
        run = self.run_bot(
            reply("looking", _read("c1"), thinking="hmm"), reply("done", thinking="so")
        )

        self.assertEqual(
            tool_results(self.script.sent), [("c1", "read https://c1.test")]
        )
        self.assertEqual(
            run.tool_data,
            [
                {
                    "assistant_content": "looking",
                    "thinking": "hmm",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "read_webpage",
                                "arguments": '{"url":"https://c1.test"}',
                            },
                        }
                    ],
                    "results": [
                        {"tool_call_id": "c1", "content": "read https://c1.test"}
                    ],
                }
            ],
        )
        # The reasoning of the answer is not a round: it is read off the run.
        self.assertEqual(run.thinking, "so")
        self.assertEqual(run.output, "done")

    def test_new_messages_are_what_the_run_added(self):
        run = self.run_bot(reply("", _read("c1")), reply("done"))

        self.assertEqual(len(run.messages), 4)
        self.assertEqual(len(run.new_messages), 3)
        self.assertIsInstance(run.new_messages[0], ModelResponse)
        self.assertEqual(
            [m["kind"] for m in run.raw_messages["messages"]],
            ["request", "response", "request", "response"],
        )

    def test_usage_sums_every_reply_of_the_run(self):
        run = self.run_bot(
            reply("", _read("c1"), input_tokens=100, output_tokens=10),
            reply("done", input_tokens=200, output_tokens=30),
        )

        self.assertEqual((run.usage.input_tokens, run.usage.output_tokens), (300, 40))
        fields = run.task_fields()
        self.assertEqual(
            (fields["prompt_tokens"], fields["completion_tokens"]), (300, 40)
        )
        self.assertEqual(fields["answer_tokens"], 30)
        self.assertEqual(fields["model_used"], "test-model")
        self.assertGreater(fields["generation_seconds"], fields["answer_seconds"])

    def test_reasoning_written_between_inline_tags_is_cut_from_the_answer(self):
        run = self.run_bot(reply("<thought>let me see</thought>The answer"))

        self.assertEqual(run.output, "The answer")
        self.assertEqual(run.thinking, "let me see")


class EmptyReplyTests(BotRunCase):
    def test_an_empty_reply_is_sent_back_to_the_model_once(self):
        run = self.run_bot(reply(""), reply("Here I am"))

        self.assertEqual(run.output, "Here I am")
        self.assertEqual(len(self.script.requests), 2)

    def test_a_second_empty_reply_fails_the_run(self):
        with self.assertRaises(UnexpectedModelBehavior):
            self.run_bot(reply(""), repeat=True)

        self.assertEqual(len(self.script.requests), 2)

    def test_a_reply_that_is_media_alone_is_an_answer(self):
        def picture(messages, tools):
            return reply("", call("write_note", {}, "c1"))

        run = self.run_bot(picture, reply(""), context={"images": [{"data": b"x"}]})

        self.assertEqual(run.output, "")
        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(len(self.script.requests), 2)

    def test_a_run_nobody_waits_on_may_say_nothing(self):
        run = self.run_bot(reply(""), expects_answer=False)

        self.assertEqual(run.output, "")
        self.assertEqual(len(self.script.requests), 1)


@override_settings(AI_MAX_TOOL_ROUNDS=2, AI_MAX_IDENTICAL_TOOL_CALLS=10)
class RoundCapTests(BotRunCase):
    def test_the_tools_are_taken_away_once_the_rounds_are_spent(self):
        def model(messages, tools):
            return reply("", _write(f"c{len(messages)}")) if tools else reply("done")

        run = self.run_bot(model, repeat=True)

        # Two rounds with tools on offer, then a request offering none.
        self.assertEqual([bool(tools) for _, tools in self.script.requests], [1, 1, 0])
        self.assertEqual(len(self.tools.executed), 2)
        self.assertIs(run.stop, StopReason.ROUND_CAP)
        self.assertEqual(run.output, "done")
        self.assertEqual(len(run.tool_data), 2)

    def test_an_answer_before_the_cap_is_not_a_cap(self):
        run = self.run_bot(reply("", _write("c1")), reply("done"))

        self.assertIs(run.stop, StopReason.ANSWERED)


@override_settings(AI_MAX_IDENTICAL_TOOL_CALLS=3)
class RepeatedToolCallTests(BotRunCase):
    def test_identical_call_runs_at_most_the_configured_number_of_times(self):
        def model(messages, tools):
            return reply("", _write(f"c{len(messages)}")) if tools else reply("done")

        run = self.run_bot(model, repeat=True)

        self.assertEqual(len(self.tools.executed), 3)
        self.assertIs(run.stop, StopReason.REPEAT_LOOP)
        self.assertEqual(run.output, "done")
        # Three rounds that ran, one that was refused, then no tool on offer.
        self.assertEqual(
            [bool(tools) for _, tools in self.script.requests], [1, 1, 1, 1, 0]
        )

    def test_refused_call_reports_why_to_the_model(self):
        def model(messages, tools):
            return reply("", _write(f"c{len(messages)}")) if tools else reply("done")

        run = self.run_bot(model, repeat=True)

        refused = run.tool_data[3]["results"][0]
        self.assertEqual(refused["outcome"], "failed")
        self.assertIn("Not executed", refused["content"])
        self.assertIn("write_note", refused["content"])
        self.assertEqual(tool_results(self.script.sent)[-1][1], refused["content"])

    def test_arguments_in_another_order_are_the_same_call(self):
        same = [
            call("write_note", '{"url": "https://a.test"}', "c1"),
            call("write_note", '{ "url":"https://a.test" }', "c2"),
        ]

        with override_settings(AI_MAX_IDENTICAL_TOOL_CALLS=1):
            self.run_bot(reply("", *same), reply("done"))

        self.assertEqual(self.tools.executed, ["c1"])

    def test_same_tool_with_different_arguments_is_not_a_repeat(self):
        run = self.run_bot(
            *[reply("", _write(f"c{i}", str(i))) for i in range(4)], reply("done")
        )

        self.assertEqual(len(self.tools.executed), 4)
        self.assertIs(run.stop, StopReason.ANSWERED)

    def test_a_round_mixing_a_repeat_and_new_work_continues(self):
        run = self.run_bot(
            reply("", _write("c1")),
            reply("", _write("c2")),
            reply("", _write("c3")),
            reply("", _write("c4"), _write("c5", "b")),
            reply("done"),
        )

        # The duplicate is refused, its sibling still runs, the loop goes on.
        self.assertEqual(self.tools.executed, ["c1", "c2", "c3", "c5"])
        self.assertIs(run.stop, StopReason.ANSWERED)


class AwaitingUserTests(BotRunCase):
    def test_a_tool_waiting_for_the_user_halts_the_run(self):
        run = self.run_bot(
            reply("One question", call("ask_user", {}, "c1")), repeat=True
        )

        self.assertEqual(len(self.script.requests), 1)
        self.assertIs(run.stop, StopReason.AWAITING_USER)
        self.assertEqual(run.output, "One question")
        self.assertEqual(
            run.tool_data[0]["results"], [{"tool_call_id": "c1", "content": "asked"}]
        )

    def test_a_question_without_a_word_is_still_a_reply(self):
        run = self.run_bot(reply("", call("ask_user", {}, "c1")), repeat=True)

        self.assertIs(run.stop, StopReason.AWAITING_USER)
        self.assertEqual(run.output, "")
        self.assertEqual(len(self.script.requests), 1)


class CancellationTests(BotRunCase):
    def test_a_run_cancelled_before_it_starts_asks_the_model_nothing(self):
        run = self.run_bot(reply("hello"), is_cancelled=lambda: True)

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertEqual(self.script.requests, [])

    def test_a_cancellation_keeps_the_calls_after_it_from_starting(self):
        cancelled = threading.Event()
        self.tools.gate = lambda call_id: cancelled.set()

        run = self.run_bot(
            reply("", _write("c1", "a"), _write("c2", "b"), _write("c3", "c")),
            repeat=True,
            is_cancelled=cancelled.is_set,
        )

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertEqual(self.tools.executed, ["c1"])
        self.assertEqual(len(self.script.requests), 1)

    def test_a_cancellation_read_after_the_round_buys_no_more_model_call(self):
        seen = []
        self.tools.gate = seen.append

        run = self.run_bot(
            reply("", _write("c1")), repeat=True, is_cancelled=lambda: bool(seen)
        )

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertEqual(len(self.script.requests), 1)


class ToolFailureTests(BotRunCase):
    def test_a_failing_tool_is_read_by_the_model_and_marked_on_the_record(self):
        run = self.run_bot(reply("", call("fail", {}, "c1")), reply("sorry"))

        (stored,) = run.tool_data[0]["results"]
        self.assertEqual(stored["outcome"], "failed")
        self.assertTrue(stored["content"].startswith("the service is down"))
        self.assertIn("temporary", stored["content"])
        self.assertEqual(run.output, "sorry")

    def test_a_bug_in_a_tool_fails_the_run(self):
        with self.assertRaisesMessage(RuntimeError, "tool exploded"):
            self.run_bot(reply("", call("explode", {}, "c1")), repeat=True)

    def test_a_tool_that_does_not_exist_costs_a_round_not_the_run(self):
        run = self.run_bot(reply("", call("nope", {}, "c1")), reply("done"))

        (stored,) = run.tool_data[0]["results"]
        self.assertEqual(stored["outcome"], "failed")
        self.assertIn("nope", stored["content"])
        self.assertIs(run.stop, StopReason.ANSWERED)

    def test_arguments_the_schema_refuses_are_told_to_the_model(self):
        run = self.run_bot(
            reply("", call("read_webpage", {"url": 3}, "c1")), reply("done")
        )

        (stored,) = run.tool_data[0]["results"]
        self.assertEqual(stored["outcome"], "failed")
        self.assertIn("url", stored["content"])
        self.assertEqual(self.tools.executed, [])

    @override_settings(AI_MAX_TOOL_ROUNDS=6)
    def test_wrong_calls_are_bounded_by_the_rounds(self):
        def model(messages, tools):
            if not tools:
                return reply("done")
            return reply("", call("nope", {"n": len(messages)}, f"c{len(messages)}"))

        run = self.run_bot(model, repeat=True)

        self.assertIs(run.stop, StopReason.ROUND_CAP)
        self.assertEqual(len(self.script.requests), 7)


class RecordTests(BotRunCase):
    @override_settings(AI_TOOL_RESULT_STORE_MAX_CHARS=300)
    def test_a_long_result_is_cut_in_the_middle_and_names_its_call(self):
        self.tools.gate = None
        page = "HEAD" + ("p" * 5000) + "TAIL"
        with patch.object(RecordingTools, "_run", lambda tools, ctx, result="ok": page):
            run = self.run_bot(reply("", _read("c1", "doc")), reply("done"))

        stored = run.tool_data[0]["results"][0]["content"]
        self.assertLessEqual(len(stored), 300)
        self.assertTrue(stored.startswith("HEAD"))
        self.assertTrue(stored.endswith("TAIL"))
        self.assertIn("read_webpage(https://doc.test)", stored)
        # The model itself read the page whole.
        self.assertEqual(tool_results(self.script.sent), [("c1", page)])

    def test_an_image_reaches_the_model_and_its_text_the_record(self):
        run = self.run_bot(reply("", call("show_image", {}, "c1")), reply("done"))

        self.assertEqual(
            run.tool_data[0]["results"], [{"tool_call_id": "c1", "content": "a cat"}]
        )
        shown = [
            item
            for message in self.script.sent
            if isinstance(message, ModelRequest)
            for part in message.parts
            for item in (part.content if isinstance(part.content, list) else [])
            if isinstance(item, BinaryContent)
        ]
        self.assertEqual([image.data for image in shown], [b"\x89PNG"])
        stored = str(run.raw_messages)
        self.assertIn("[stripped]", stored)

    def test_the_run_context_reaches_the_tools_and_comes_back(self):
        run = self.run_bot(
            reply("", _write("c1")), reply("done"), context={"agent_checkin": True}
        )

        self.assertEqual(
            run.context, {"agent_checkin": True, "written": ["https://a.test"]}
        )


@override_settings(AI_TOOL_CONCURRENCY=4)
class ThreadTests(BotRunCase):
    """Which thread a tool runs on decides which database connection it gets."""

    def test_a_tool_that_runs_alone_runs_on_the_thread_that_started_the_run(self):
        # Where it finds the task's connection, and in a test the open
        # transaction holding the rows the test created.
        self.run_bot(reply("", _write("w1")), reply("done"))

        self.assertEqual(self.tools.threads, {"w1": threading.get_ident()})

    def test_reads_of_one_round_run_at_the_same_time_in_pool_threads(self):
        # Both calls must be in flight together for the barrier to release.
        barrier = threading.Barrier(2, timeout=10)
        self.tools.gate = lambda call_id: barrier.wait()

        self.run_bot(reply("", _read("c1"), _read("c2")), reply("done"))

        self.assertEqual(self.tools.peak_in_flight, 2)
        self.assertNotIn(threading.get_ident(), self.tools.threads.values())

    def test_results_follow_call_order_not_completion_order(self):
        def gate(call_id):
            # The first call only returns once the second is over.
            deadline = time.monotonic() + 10
            while call_id == "c1" and ("c2", "end") not in self.tools.spans:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)

        self.tools.gate = gate

        run = self.run_bot(reply("", _read("c1"), _read("c2")), reply("done"))

        self.assertEqual(self.tools.spans[-1], ("c1", "end"))
        self.assertEqual(
            [result["tool_call_id"] for result in run.tool_data[0]["results"]],
            ["c1", "c2"],
        )

    def test_a_write_splits_the_round_and_keeps_its_place(self):
        self.run_bot(reply("", _read("r1"), _write("w1"), _read("r2")), reply("done"))

        self.assertEqual(
            self.tools.spans,
            [
                ("r1", "start"),
                ("r1", "end"),
                ("w1", "start"),
                ("w1", "end"),
                ("r2", "start"),
                ("r2", "end"),
            ],
        )

    @override_settings(AI_TOOL_CONCURRENCY=1)
    def test_a_concurrency_of_one_keeps_every_tool_on_the_task_thread(self):
        self.run_bot(reply("", _read("c1"), _read("c2")), reply("done"))

        self.assertEqual(self.tools.peak_in_flight, 1)
        self.assertEqual(set(self.tools.threads.values()), {threading.get_ident()})

    def test_a_pool_thread_hands_its_database_connections_back(self):
        with patch("workspace.ai.tool_registry.connections") as connections:
            self.run_bot(
                reply("", _read("c1"), _read("c2"), _write("w1")), reply("done")
            )

        self.assertEqual(connections.close_all.call_count, 2)


@override_settings(AI_TOOL_CONCURRENCY=4)
class LanguageTests(BotRunCase):
    def tearDown(self):
        cache.clear()

    def test_a_tool_runs_in_the_users_language_on_either_thread(self):
        user = User.objects.create_user(username="alice", password="pw")
        set_setting(user, "core", "language", "fr")

        self.run_bot(
            reply("", _write("w1"), _read("c1"), _read("c2")), reply("done"), user=user
        )

        self.assertEqual(self.tools.languages, {"w1": "fr", "c1": "fr", "c2": "fr"})


class StepTests(BotRunCase):
    """Each call is shown to the conversation while it runs."""

    def run_watched(self, *replies, **options):
        with (
            patch("workspace.ai.services.bot_runs.step_recipients", return_value=[7]),
            patch("workspace.ai.services.bot_runs.notify_tool_step") as started,
            patch("workspace.ai.services.bot_runs.notify_tool_step_done") as done,
        ):
            self.run_bot(*replies, conversation_id="conv-1", **options)
        return (
            [c.args[2].tool_call_id for c in started.call_args_list],
            [c.args[2].tool_call_id for c in done.call_args_list],
        )

    def test_each_call_is_announced_then_reported_done(self):
        started, done = self.run_watched(
            reply("", _write("w1"), _read("c1")), reply("ok")
        )

        self.assertEqual(started, ["w1", "c1"])
        self.assertEqual(done, ["w1", "c1"])

    def test_a_call_that_fails_is_still_reported_done(self):
        started, done = self.run_watched(reply("", call("fail", {}, "c1")), reply("ok"))

        self.assertEqual((started, done), (["c1"], ["c1"]))

    @override_settings(AI_MAX_IDENTICAL_TOOL_CALLS=1)
    def test_a_refused_repeat_announces_nothing(self):
        started, done = self.run_watched(
            reply("", _write("c1"), _write("c2")), reply("ok")
        )

        self.assertEqual((started, done), (["c1"], ["c1"]))

    def test_a_run_outside_a_conversation_shows_nobody_anything(self):
        with patch("workspace.ai.services.bot_runs.notify_tool_step") as started:
            self.run_bot(reply("", _write("w1")), reply("ok"))

        started.assert_not_called()


@override_settings(AI_API_KEY="test-key", CELERY_TASK_ALWAYS_EAGER=True)
class ChatReplyTests(TestCase):
    """The chat task end to end, with the registered tools."""

    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(username="bot", password="pass123")
        self.bot_profile = BotProfile.objects.create(
            user=self.bot_user, system_prompt="You are a test bot.", is_public=True
        )
        # Titled, so that a reply does not go on to ask a model for a title.
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user, title="Test"
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.user
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.bot_user
        )
        self.message = Message.objects.create(
            conversation=self.conversation, author=self.user, body="My name is Pierre"
        )

    def tearDown(self):
        cache.clear()

    def test_a_tool_writes_through_the_tasks_own_connection_then_the_bot_answers(self):
        with scripted(
            reply(
                "",
                call("save_memory", {"key": "name", "content": "Pierre"}, "call_abc"),
                input_tokens=20,
                output_tokens=10,
            ),
            reply("Got it, Pierre!", input_tokens=30, output_tokens=8),
        ) as script:
            result = generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        self.assertEqual(result["status"], "ok", result)
        # Written inside this test's transaction: the tool ran on the thread
        # that started the run, not on one with a connection of its own.
        memory = UserMemory.objects.get(user=self.user, bot=self.bot_user, key="name")
        self.assertEqual(memory.content, "Pierre")
        bot_message = Message.objects.get(author=self.bot_user)
        self.assertEqual(bot_message.body, "Got it, Pierre!")
        (round_,) = bot_message.tool_data
        self.assertEqual(round_["tool_calls"][0]["function"]["name"], "save_memory")
        self.assertEqual(round_["results"][0]["tool_call_id"], "call_abc")
        task = AITask.objects.get(chat_message=bot_message)
        self.assertEqual(task.status, AITask.Status.COMPLETED)
        self.assertEqual((task.prompt_tokens, task.completion_tokens), (50, 18))
        self.assertEqual(task.model_used, "test-model")
        self.assertEqual(len(task.raw_messages["messages"]), 4)
        self.assertIn("save_memory", script.tools)

    def test_the_model_is_sent_the_chat_prompt_and_the_users_message(self):
        with scripted(reply("Hello")) as script:
            generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        (request,) = script.sent
        self.assertIn("You are a test bot.", request.parts[0].content)
        self.assertIn("My name is Pierre", [part.content for part in request.parts])
