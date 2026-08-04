from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.ai.services.tool_loop import run_tool_loop

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

        # 1 initial call + one re-call per allowed round
        self.assertEqual(mock_call_llm.call_count, 3)
        self.assertEqual(reg.execute.call_count, 2)
        # The final entry is the captured last response, not a tool round
        self.assertEqual(len(rounds), 3)
        self.assertNotIn("tool_executions", rounds[-1])


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
    @patch(
        "workspace.ai.services.tool_loop.step_recipients", return_value=[1, 2]
    )
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
        mock_notify.assert_called_once_with([], None, tool_call)
