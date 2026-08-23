from types import SimpleNamespace
from unittest.mock import patch

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

    @patch("workspace.ai.services.tool_loop.notify_tool_step")
    @patch("workspace.ai.services.tool_loop.step_recipients", return_value=[1, 2])
    @patch("workspace.ai.services.tool_loop.call_llm")
    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    def test_each_tool_execution_emits_a_step(
        self, mock_build, mock_call_llm, mock_recipients, mock_notify
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

        mock_recipients.assert_called_once_with("conv-1", self.bot)
        mock_notify.assert_called_once_with([1, 2], "conv-1", tool_call)

    @patch("workspace.ai.services.tool_loop.notify_tool_step")
    @patch("workspace.ai.services.tool_loop.step_recipients")
    @patch("workspace.ai.services.tool_loop.call_llm")
    @patch("workspace.ai.services.tool_loop.build_tool_content", return_value="ok")
    def test_no_conversation_id_skips_recipient_lookup(
        self, mock_build, mock_call_llm, mock_recipients, mock_notify
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
