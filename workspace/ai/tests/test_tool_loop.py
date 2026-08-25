import threading
from types import SimpleNamespace
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.ai.services.tool_loop import run_tool_loop
from workspace.chat.models import Conversation, ConversationMember

User = get_user_model()


class RoundCapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dave", email="d@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="bot3", email="b3@test.com", password="pw"
        )

    @override_settings(AI_MAX_TOOL_ROUNDS=2)
    @patch("workspace.ai.services.tool_loop.call_llm")
    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    def test_loop_stops_at_configured_round_cap(self, mock_build, mock_call_llm):
        tool_call = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="search", arguments="{}"),
        )
        msg = SimpleNamespace(role="assistant", content="", tool_calls=[tool_call])
        looping_result = {
            "tool_calls": [tool_call],
            "content": "",
            "message": msg,
            "model": "x",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        mock_call_llm.return_value = looping_result

        with patch("workspace.ai.tool_registry.tool_registry") as reg:
            reg.get_definitions.return_value = []
            reg.execute.return_value = "ok"
            result, ctx, rounds, td = run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=None,
            )

        # 1 initial call + one re-call per allowed round + the tool-less call
        # that turns the exhausted run into an answer
        self.assertEqual(mock_call_llm.call_count, 4)
        self.assertEqual(reg.execute.call_count, 2)
        self.assertEqual(ctx["round_cap_reached"], True)
        self.assertEqual(mock_call_llm.call_args.kwargs.get("tools"), None)
        # The final entry is the captured last response, not a tool round
        self.assertEqual(len(rounds), 4)
        self.assertNotIn("tool_executions", rounds[-1])

    @override_settings(AI_MAX_TOOL_ROUNDS=2)
    @patch("workspace.ai.services.tool_loop.call_llm")
    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    def test_no_extra_call_when_cap_lands_on_a_text_answer(
        self, mock_build, mock_call_llm
    ):
        tool_call = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="search", arguments="{}"),
        )
        msg = SimpleNamespace(role="assistant", content="", tool_calls=[tool_call])
        looping_result = {
            "tool_calls": [tool_call],
            "content": "",
            "message": msg,
            "model": "x",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        final_result = {
            "tool_calls": [],
            "content": "done",
            "message": SimpleNamespace(role="assistant", content="done", tool_calls=[]),
            "model": "x",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        mock_call_llm.side_effect = [looping_result, looping_result, final_result]

        with patch("workspace.ai.tool_registry.tool_registry") as reg:
            reg.get_definitions.return_value = []
            reg.execute.return_value = "ok"
            result, ctx, rounds, td = run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=None,
            )

        self.assertEqual(mock_call_llm.call_count, 3)
        self.assertEqual(result["content"], "done")
        # The cap was spent, but the run ended on a real answer: nothing was
        # cut short, so the flag callers use to spot truncated runs stays off.
        self.assertFalse(ctx.get("round_cap_reached"))


class StopAfterRoundTests(TestCase):
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

    @patch("workspace.ai.services.tool_loop.call_llm")
    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    def test_stop_after_round_halts_loop(self, mock_build, mock_call_llm):
        tool_call = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="halt_tool", arguments="{}"),
        )
        msg = SimpleNamespace(
            role="assistant",
            content="",
            tool_calls=[tool_call],
        )
        first_result = {
            "tool_calls": [tool_call],
            "content": "",
            "message": msg,
            "model": "x",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        mock_call_llm.return_value = first_result

        def fake_execute(tc, user, bot, conversation_id, context):
            context["stop_after_round"] = True
            return "ok"

        with patch("workspace.ai.tool_registry.tool_registry") as reg:
            reg.get_definitions.return_value = []
            reg.execute.side_effect = fake_execute

            result, ctx, rounds, td = run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=None,
            )

        self.assertEqual(mock_call_llm.call_count, 1)
        self.assertTrue(ctx.get("stop_after_round"))
        self.assertEqual(rounds[-1].get("terminated_by_tool"), True)


class ThinkingPersistenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="carol", email="c@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="bot2", email="b2@test.com", password="pw"
        )

    @patch("workspace.ai.services.tool_loop.call_llm")
    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    def test_round_thinking_is_stored_in_tool_data(self, mock_build, mock_call_llm):
        tool_call = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="search", arguments="{}"),
        )
        msg = SimpleNamespace(role="assistant", content="", tool_calls=[tool_call])
        first = {
            "tool_calls": [tool_call],
            "content": "",
            "thinking": "I should search first",
            "message": msg,
            "model": "x",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        final = {
            "tool_calls": None,
            "content": "Done",
            "thinking": "now I can answer",
            "message": SimpleNamespace(
                role="assistant", content="Done", tool_calls=None
            ),
            "model": "x",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        mock_call_llm.side_effect = [first, final]

        with patch("workspace.ai.tool_registry.tool_registry") as reg:
            reg.get_definitions.return_value = []
            reg.execute.return_value = "ok"
            result, ctx, rounds, td = run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=None,
            )

        self.assertEqual(len(td), 1)
        self.assertEqual(td[0]["thinking"], "I should search first")
        # Final-response thinking is NOT in tool_data (post_bot_message appends it)
        self.assertEqual(result["thinking"], "now I can answer")


class StepEmissionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="erin", email="e@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="bot4", email="b4@test.com", password="pw"
        )
        self.leaver = User.objects.create_user(
            username="frank", email="f@test.com", password="pw"
        )

    def _results(self, tool_call):
        msg = SimpleNamespace(role="assistant", content="", tool_calls=[tool_call])
        first = {
            "tool_calls": [tool_call],
            "content": "",
            "message": msg,
            "model": "x",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        final = {
            "tool_calls": None,
            "content": "Done",
            "message": SimpleNamespace(
                role="assistant", content="Done", tool_calls=None
            ),
            "model": "x",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        return first, final

    @patch("workspace.ai.services.tool_loop.notify_tool_step_done")
    @patch("workspace.ai.services.tool_loop.notify_tool_step")
    @patch("workspace.ai.services.tool_loop.step_recipients", return_value=[1, 2])
    @patch("workspace.ai.services.tool_loop.call_llm")
    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    def test_each_tool_execution_emits_a_step(
        self, mock_build, mock_call_llm, mock_recipients, mock_notify, mock_done
    ):
        tool_call = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="search", arguments="{}"),
        )
        mock_call_llm.side_effect = self._results(tool_call)

        with patch("workspace.ai.tool_registry.tool_registry") as reg:
            reg.get_definitions.return_value = []
            reg.execute.return_value = "ok"
            run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id="conv-1",
            )

        # Membership is re-read for the completion too: a member who leaves
        # while a tool runs must not receive its end either.
        self.assertEqual(mock_recipients.call_args_list, [call("conv-1", self.bot)] * 2)
        mock_notify.assert_called_once_with([1, 2], "conv-1", tool_call)
        mock_done.assert_called_once_with([1, 2], "conv-1", tool_call)

    @patch("workspace.ai.services.tool_loop.notify_tool_step_done")
    @patch("workspace.ai.services.tool_loop.notify_tool_step")
    @patch("workspace.ai.services.tool_loop.step_recipients")
    @patch("workspace.ai.services.tool_loop.call_llm")
    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    def test_no_conversation_id_skips_recipient_lookup(
        self, mock_build, mock_call_llm, mock_recipients, mock_notify, mock_done
    ):
        tool_call = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="search", arguments="{}"),
        )
        mock_call_llm.side_effect = self._results(tool_call)

        with patch("workspace.ai.tool_registry.tool_registry") as reg:
            reg.get_definitions.return_value = []
            reg.execute.return_value = "ok"
            run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=None,
            )

        mock_recipients.assert_not_called()
        mock_notify.assert_not_called()
        mock_done.assert_not_called()

    @patch("workspace.ai.services.tool_loop.notify_tool_step")
    @patch("workspace.ai.services.tool_loop.call_llm")
    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    def test_member_leaving_mid_run_stops_receiving_steps(
        self, mock_build, mock_call_llm, mock_notify
    ):
        """Recipients are re-read per tool, not snapshotted for the whole run."""
        conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        for member in (self.user, self.leaver, self.bot):
            ConversationMember.objects.create(conversation=conversation, user=member)

        first_call = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="search", arguments="{}"),
        )
        second_call = SimpleNamespace(
            id="call_2",
            type="function",
            function=SimpleNamespace(name="search", arguments="{}"),
        )
        first, final = self._results(second_call)
        mock_call_llm.side_effect = [self._results(first_call)[0], first, final]

        def leave_after_first_tool(*args, **kwargs):
            ConversationMember.objects.filter(
                conversation=conversation, user=self.leaver
            ).update(left_at=timezone.now())
            return "ok"

        with patch("workspace.ai.tool_registry.tool_registry") as reg:
            reg.get_definitions.return_value = []
            reg.execute.side_effect = leave_after_first_tool
            run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=conversation.pk,
            )

        delivered = [set(call.args[0]) for call in mock_notify.call_args_list]
        self.assertEqual(delivered, [{self.user.id, self.leaver.id}, {self.user.id}])


def _tool_call(call_id, name="search", arguments="{}"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _llm_result(tool_calls, content=""):
    return {
        "tool_calls": tool_calls,
        "content": content,
        "message": SimpleNamespace(
            role="assistant", content=content, tool_calls=tool_calls
        ),
        "model": "x",
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


@override_settings(AI_MAX_TOOL_ROUNDS=10, AI_MAX_IDENTICAL_TOOL_CALLS=3)
class RepeatedToolCallTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="repeat-user", email="ru@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="repeat-bot", email="rb@test.com", password="pw"
        )

    def _run(self, mock_call_llm, reg_execute="ok"):
        with patch("workspace.ai.tool_registry.tool_registry") as reg:
            reg.get_definitions.return_value = []
            reg.execute.return_value = reg_execute
            result, ctx, rounds, td = run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=None,
            )
        return reg, result, ctx, rounds

    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_identical_call_runs_at_most_the_configured_number_of_times(
        self, mock_call_llm, mock_build
    ):
        mock_call_llm.return_value = _llm_result([_tool_call("c1")])

        reg, result, ctx, rounds = self._run(mock_call_llm)

        self.assertEqual(reg.execute.call_count, 3)
        self.assertEqual(ctx["repeat_loop_stopped"], True)

    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_refused_call_reports_why_to_the_model(self, mock_call_llm, mock_build):
        mock_call_llm.return_value = _llm_result([_tool_call("c1")])

        _, _, _, rounds = self._run(mock_call_llm)

        refused = rounds[3]["tool_executions"][0]["result"]
        self.assertIn("Not executed", refused)
        self.assertIn("search", refused)

    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_repeat_loop_ends_on_a_tool_less_answer(self, mock_call_llm, mock_build):
        looping = _llm_result([_tool_call("c1")])
        mock_call_llm.side_effect = [looping] * 4 + [_llm_result([], content="done")]

        _, result, ctx, rounds = self._run(mock_call_llm)

        self.assertEqual(result["content"], "done")
        self.assertIsNone(mock_call_llm.call_args.kwargs.get("tools"))
        self.assertEqual(rounds[-2]["repeat_loop_stopped"], True)

    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_same_tool_with_different_arguments_is_not_a_repeat(
        self, mock_call_llm, mock_build
    ):
        mock_call_llm.side_effect = [
            _llm_result([_tool_call("c1", arguments='{"url": "https://a.com"}')]),
            _llm_result([_tool_call("c2", arguments='{"url": "https://b.com"}')]),
            _llm_result([_tool_call("c3", arguments='{"url": "https://c.com"}')]),
            _llm_result([_tool_call("c4", arguments='{"url": "https://d.com"}')]),
            _llm_result([], content="done"),
        ]

        reg, result, ctx, rounds = self._run(mock_call_llm)

        self.assertEqual(reg.execute.call_count, 4)
        self.assertNotIn("repeat_loop_stopped", ctx)
        self.assertEqual(result["content"], "done")

    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_reordered_arguments_count_as_the_same_call(
        self, mock_call_llm, mock_build
    ):
        mock_call_llm.side_effect = [
            _llm_result([_tool_call("c1", arguments='{"a": 1, "b": 2}')]),
            _llm_result([_tool_call("c2", arguments='{"b": 2, "a": 1}')]),
            _llm_result([_tool_call("c3", arguments='{"a": 1, "b": 2}')]),
            _llm_result([_tool_call("c4", arguments='{"b": 2, "a": 1}')]),
            _llm_result([], content="done"),
        ]

        reg, _, ctx, _ = self._run(mock_call_llm)

        self.assertEqual(reg.execute.call_count, 3)
        self.assertEqual(ctx["repeat_loop_stopped"], True)

    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_a_round_mixing_a_repeat_and_new_work_continues(
        self, mock_call_llm, mock_build
    ):
        repeated = '{"url": "https://a.com"}'
        mock_call_llm.side_effect = [
            _llm_result([_tool_call("c1", arguments=repeated)]),
            _llm_result([_tool_call("c2", arguments=repeated)]),
            _llm_result([_tool_call("c3", arguments=repeated)]),
            _llm_result(
                [
                    _tool_call("c4", arguments=repeated),
                    _tool_call("c5", arguments='{"url": "https://b.com"}'),
                ]
            ),
            _llm_result([], content="done"),
        ]

        reg, result, ctx, _ = self._run(mock_call_llm)

        # The duplicate is refused, its sibling still runs, the loop goes on.
        self.assertEqual(reg.execute.call_count, 4)
        self.assertNotIn("repeat_loop_stopped", ctx)
        self.assertEqual(result["content"], "done")


@override_settings(
    AI_MAX_TOOL_ROUNDS=10,
    AI_TOOL_RESULT_TASK_MAX_CHARS=300,
    AI_TOOL_RESULT_STORE_MAX_CHARS=3000,
)
class ResultTruncationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="trunc-user", email="tu@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="trunc-bot", email="tb@test.com", password="pw"
        )

    @patch(
        "workspace.ai.services.tool_loop.build_tool_content", side_effect=lambda r: r
    )
    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_replayable_history_keeps_far_more_than_the_debug_record(
        self, mock_call_llm, mock_build
    ):
        page = "HEAD" + ("p" * 20000) + "TAIL"
        mock_call_llm.side_effect = [
            _llm_result(
                [
                    _tool_call(
                        "c1",
                        name="fetch_url",
                        arguments='{"url": "https://example.com/doc"}',
                    )
                ]
            ),
            _llm_result([], content="done"),
        ]

        with patch("workspace.ai.tool_registry.tool_registry") as reg:
            reg.get_definitions.return_value = []
            reg.execute.return_value = page
            reg.describe_call.return_value = "fetch_url(https://example.com/doc)"
            _, _, rounds, tool_data = run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=None,
            )

        debug_result = rounds[0]["tool_executions"][0]["result"]
        replayed = tool_data[0]["results"][0]["content"]
        self.assertLessEqual(len(debug_result), 300)
        self.assertLessEqual(len(replayed), 3000)
        self.assertGreater(len(replayed), len(debug_result))
        # Both are cut in the middle and point back at the call that made them.
        for text in (debug_result, replayed):
            self.assertTrue(text.startswith("HEAD"))
            self.assertTrue(text.endswith("TAIL"))
            self.assertIn("fetch_url(https://example.com/doc)", text)


class _StubRegistry:
    """Tool registry recording when each call starts and ends.

    Handlers run for real (in a pool thread when the loop batches them), so
    the recorded spans are the actual execution order rather than a
    reconstruction of it.
    """

    def __init__(self, concurrent=(), handler=None):
        self._concurrent = frozenset(concurrent)
        self._handler = handler or (lambda tc: f"result {tc.id}")
        self.spans = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._lock = threading.Lock()

    def get_definitions(self):
        return []

    def concurrent_names(self):
        return self._concurrent

    def describe_call(self, name, raw_arguments, max_len=120):
        return name

    def execute(self, tool_call, user, bot, conversation_id=None, context=None):
        self._enter(tool_call.id)
        try:
            return self._handler(tool_call)
        finally:
            self._leave(tool_call.id)

    def _enter(self, call_id):
        with self._lock:
            self.spans.append((call_id, "start"))
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)

    def _leave(self, call_id):
        with self._lock:
            self.in_flight -= 1
            self.spans.append((call_id, "end"))


@override_settings(AI_MAX_TOOL_ROUNDS=10, AI_MAX_IDENTICAL_TOOL_CALLS=3)
class ConcurrentToolCallTests(TestCase):
    """Read-only calls of one round share a dispatch; everything else doesn't."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="conc-user", email="cu@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="conc-bot", email="cb@test.com", password="pw"
        )

    def _run(self, registry, calls, is_cancelled=None):
        messages = [{"role": "user", "content": "go"}]
        with patch("workspace.ai.services.tool_loop.call_llm") as mock_call_llm:
            mock_call_llm.side_effect = [
                _llm_result(calls),
                _llm_result([], content="done"),
            ]
            with patch("workspace.ai.tool_registry.tool_registry", registry):
                _, ctx, rounds, tool_data = run_tool_loop(
                    messages=messages,
                    model="x",
                    human_user=self.user,
                    bot_user=self.bot,
                    conversation_id=None,
                    is_cancelled=is_cancelled,
                )
        return ctx, rounds, tool_data, messages

    def _tool_messages(self, messages):
        return [
            (m["tool_call_id"], m["content"]) for m in messages if m["role"] == "tool"
        ]

    def test_reads_in_one_round_run_at_the_same_time(self):
        # Both calls must be in flight together for the barrier to release;
        # a sequential loop breaks it and the run fails instead of passing
        # slowly.
        barrier = threading.Barrier(2, timeout=10)
        registry = _StubRegistry(
            concurrent={"read_webpage"},
            handler=lambda tc: barrier.wait() and "" or "read",
        )
        calls = [
            _tool_call(
                "c1", name="read_webpage", arguments='{"url": "https://a.test"}'
            ),
            _tool_call(
                "c2", name="read_webpage", arguments='{"url": "https://b.test"}'
            ),
        ]

        self._run(registry, calls)

        self.assertEqual(registry.peak_in_flight, 2)

    def test_results_follow_call_order_not_completion_order(self):
        finished_second = threading.Event()

        def handler(tool_call):
            if tool_call.id == "c1":
                finished_second.wait(10)
                return "first"
            finished_second.set()
            return "second"

        registry = _StubRegistry(concurrent={"read_webpage"}, handler=handler)
        calls = [
            _tool_call(
                "c1", name="read_webpage", arguments='{"url": "https://a.test"}'
            ),
            _tool_call(
                "c2", name="read_webpage", arguments='{"url": "https://b.test"}'
            ),
        ]

        _, rounds, tool_data, messages = self._run(registry, calls)

        # c2 finished first, yet the model reads the results in the order it
        # asked for them.
        self.assertEqual(registry.spans[-2], ("c2", "end"))
        self.assertEqual(
            self._tool_messages(messages), [("c1", "first"), ("c2", "second")]
        )
        self.assertEqual(
            [e["tool_call_id"] for e in rounds[0]["tool_executions"]], ["c1", "c2"]
        )
        self.assertEqual(
            [r["tool_call_id"] for r in tool_data[0]["results"]], ["c1", "c2"]
        )

    def test_a_write_splits_the_round_and_keeps_its_place(self):
        registry = _StubRegistry(concurrent={"read_webpage"})
        calls = [
            _tool_call(
                "r1", name="read_webpage", arguments='{"url": "https://a.test"}'
            ),
            _tool_call("w1", name="send_user_message", arguments="{}"),
            _tool_call(
                "r2", name="read_webpage", arguments='{"url": "https://b.test"}'
            ),
        ]

        _, _, _, messages = self._run(registry, calls)

        self.assertEqual(
            registry.spans,
            [
                ("r1", "start"),
                ("r1", "end"),
                ("w1", "start"),
                ("w1", "end"),
                ("r2", "start"),
                ("r2", "end"),
            ],
        )
        self.assertEqual(
            [call_id for call_id, _ in self._tool_messages(messages)],
            ["r1", "w1", "r2"],
        )

    @override_settings(AI_TOOL_CONCURRENCY=2)
    def test_a_batch_never_grows_past_the_configured_width(self):
        registry = _StubRegistry(concurrent={"read_webpage"})
        calls = [
            _tool_call(
                f"c{i}", name="read_webpage", arguments=f'{{"url": "https://{i}.test"}}'
            )
            for i in range(5)
        ]

        self._run(registry, calls)

        self.assertLessEqual(registry.peak_in_flight, 2)
        # The sixth call waits for the pair before it: batches are dispatched
        # one after another, only their members overlap.
        starts = [call_id for call_id, event in registry.spans if event == "start"]
        self.assertEqual(starts, ["c0", "c1", "c2", "c3", "c4"])
        third_start = registry.spans.index(("c2", "start"))
        self.assertLess(registry.spans.index(("c0", "end")), third_start)
        self.assertLess(registry.spans.index(("c1", "end")), third_start)

    @override_settings(AI_TOOL_CONCURRENCY=1)
    def test_concurrency_of_one_runs_reads_one_by_one(self):
        registry = _StubRegistry(concurrent={"read_webpage"})
        calls = [
            _tool_call(
                "c1", name="read_webpage", arguments='{"url": "https://a.test"}'
            ),
            _tool_call(
                "c2", name="read_webpage", arguments='{"url": "https://b.test"}'
            ),
        ]

        self._run(registry, calls)

        self.assertEqual(registry.peak_in_flight, 1)

    @override_settings(AI_TOOL_CONCURRENCY=2)
    def test_a_cancellation_stops_the_next_batch_from_starting(self):
        cancelled = threading.Event()

        def handler(tool_call):
            if tool_call.id == "c2":
                cancelled.set()
            return "ok"

        registry = _StubRegistry(concurrent={"read_webpage"}, handler=handler)
        calls = [
            _tool_call(
                f"c{i}", name="read_webpage", arguments=f'{{"url": "https://{i}.test"}}'
            )
            for i in range(1, 5)
        ]

        ctx, _, tool_data, messages = self._run(
            registry, calls, is_cancelled=cancelled.is_set
        )

        self.assertTrue(ctx["cancelled"])
        self.assertEqual(
            [call_id for call_id, _ in self._tool_messages(messages)], ["c1", "c2"]
        )
        self.assertEqual(
            [r["tool_call_id"] for r in tool_data[0]["results"]], ["c1", "c2"]
        )

    def test_a_cancelled_run_dispatches_nothing(self):
        registry = _StubRegistry(concurrent={"read_webpage"})
        calls = [
            _tool_call(
                "c1", name="read_webpage", arguments='{"url": "https://a.test"}'
            ),
            _tool_call(
                "c2", name="read_webpage", arguments='{"url": "https://b.test"}'
            ),
        ]

        ctx, _, _, _ = self._run(registry, calls, is_cancelled=lambda: True)

        self.assertTrue(ctx["cancelled"])
        self.assertEqual(registry.spans, [])

    def test_a_repeated_call_is_still_refused_inside_a_batch(self):
        registry = _StubRegistry(concurrent={"read_webpage"})
        repeated = '{"url": "https://a.test"}'
        calls = [
            _tool_call("c1", name="read_webpage", arguments=repeated),
            _tool_call("c2", name="read_webpage", arguments=repeated),
            _tool_call("c3", name="read_webpage", arguments=repeated),
            _tool_call("c4", name="read_webpage", arguments=repeated),
            _tool_call(
                "c5", name="read_webpage", arguments='{"url": "https://b.test"}'
            ),
        ]

        _, _, _, messages = self._run(registry, calls)

        executed = [call_id for call_id, event in registry.spans if event == "start"]
        self.assertEqual(sorted(executed), ["c1", "c2", "c3", "c5"])
        recorded = self._tool_messages(messages)
        self.assertEqual(
            [call_id for call_id, _ in recorded], ["c1", "c2", "c3", "c4", "c5"]
        )
        self.assertIn("Not executed", dict(recorded)["c4"])

    def test_a_failing_call_takes_the_batch_down_with_it(self):
        def handler(tool_call):
            if tool_call.id == "c2":
                raise RuntimeError("tool exploded")
            return "ok"

        registry = _StubRegistry(concurrent={"read_webpage"}, handler=handler)
        calls = [
            _tool_call(
                "c1", name="read_webpage", arguments='{"url": "https://a.test"}'
            ),
            _tool_call(
                "c2", name="read_webpage", arguments='{"url": "https://b.test"}'
            ),
        ]

        with self.assertRaises(RuntimeError):
            self._run(registry, calls)


@override_settings(AI_MAX_TOOL_ROUNDS=10, AI_MAX_IDENTICAL_TOOL_CALLS=3)
class StepCompletionTests(TestCase):
    """Each call reports its own end, so the UI never guesses from position."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="step-user", email="su@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="step-bot", email="sb@test.com", password="pw"
        )

    def _run(self, registry, calls, conversation_id="conv-1"):
        events = registry.spans
        with (
            patch("workspace.ai.services.tool_loop.step_recipients", return_value=[7]),
            patch(
                "workspace.ai.services.tool_loop.notify_tool_step",
                side_effect=lambda ids, conv, tc: events.append((tc.id, "step")),
            ),
            patch(
                "workspace.ai.services.tool_loop.notify_tool_step_done",
                side_effect=lambda ids, conv, tc: events.append((tc.id, "done")),
            ),
            patch("workspace.ai.services.tool_loop.call_llm") as mock_call_llm,
            patch("workspace.ai.tool_registry.tool_registry", registry),
        ):
            mock_call_llm.side_effect = [
                _llm_result(calls),
                _llm_result([], content="done"),
            ]
            run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=conversation_id,
            )
        return events

    def test_a_call_reports_its_end_once_it_has_returned(self):
        registry = _StubRegistry()
        calls = [_tool_call("c1", name="get_weather", arguments='{"location": "Lyon"}')]

        events = self._run(registry, calls)

        self.assertEqual(
            events, [("c1", "step"), ("c1", "start"), ("c1", "end"), ("c1", "done")]
        )

    def test_the_call_that_ends_first_reports_first(self):
        # The whole point of the completion event: c2 was dispatched second
        # and finished first, and its row has to stop spinning right then -
        # not when c1, the slow one it shares a batch with, catches up.
        finished_second = threading.Event()

        def handler(tool_call):
            if tool_call.id == "c1":
                finished_second.wait(10)
                return "slow"
            finished_second.set()
            return "fast"

        registry = _StubRegistry(concurrent={"read_webpage"}, handler=handler)
        calls = [
            _tool_call(
                "c1", name="read_webpage", arguments='{"url": "https://a.test"}'
            ),
            _tool_call(
                "c2", name="read_webpage", arguments='{"url": "https://b.test"}'
            ),
        ]

        events = self._run(registry, calls)

        # Both rows are announced in call order before either runs.
        self.assertEqual(events[:2], [("c1", "step"), ("c2", "step")])
        self.assertLess(events.index(("c2", "done")), events.index(("c1", "end")))

    def test_a_refused_repeat_announces_nothing_and_ends_nothing(self):
        registry = _StubRegistry()
        repeated = '{"location": "Lyon"}'
        calls = [
            _tool_call(f"c{i}", name="get_weather", arguments=repeated)
            for i in range(1, 5)
        ]

        events = self._run(registry, calls)

        # The fourth identical call is refused before it could run, so its
        # row never appears and never has to be ended.
        self.assertEqual([call_id for call_id, _ in events if call_id == "c4"], [])
        self.assertEqual(
            events[-4:],
            [("c3", "step"), ("c3", "start"), ("c3", "end"), ("c3", "done")],
        )

    def test_a_run_outside_a_conversation_notifies_nobody(self):
        registry = _StubRegistry()
        calls = [_tool_call("c1", name="get_weather", arguments="{}")]

        with (
            patch("workspace.ai.services.tool_loop.notify_tool_step") as mock_step,
            patch("workspace.ai.services.tool_loop.notify_tool_step_done") as mock_done,
            patch("workspace.ai.services.tool_loop.call_llm") as mock_call_llm,
            patch("workspace.ai.tool_registry.tool_registry", registry),
        ):
            mock_call_llm.side_effect = [
                _llm_result(calls),
                _llm_result([], content="done"),
            ]
            run_tool_loop(
                messages=[{"role": "user", "content": "go"}],
                model="x",
                human_user=self.user,
                bot_user=self.bot,
                conversation_id=None,
            )

        mock_step.assert_not_called()
        mock_done.assert_not_called()
